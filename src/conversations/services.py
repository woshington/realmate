import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from assistant import CLOSING_MESSAGE
from assistant.agent import create_conversation
from conversations.enums import ConversationStatus, MessageRole
from conversations.models import Conversation, Message
from properties.models import Property


@dataclass(frozen=True, slots=True)
class MessageIngestion:
    message: Message
    conversation: Conversation
    created: bool


@transaction.atomic
def register_message(
    *,
    external_id: UUID,
    user_phone: str,
    content: str,
    timestamp: datetime,
    role: str = MessageRole.CUSTOMER,
) -> MessageIngestion:

    conversation, _ = Conversation.objects.get_or_create(user_phone=user_phone)

    message, created = Message.objects.get_or_create(
        external_id=external_id,
        defaults={
            "conversation": conversation,
            "content": content,
            "role": role,
            "timestamp": timestamp,
        },
    )

    if created:
        # Só mensagem nova do cliente reabre: a resposta do assistente também
        # passa por aqui, e a própria mensagem de encerramento desfaria o
        # encerramento. Reentrega de mensagem antiga também não reabre.
        if role == MessageRole.CUSTOMER:
            reopen_if_closed(conversation)

        touch_last_message_at(conversation, timestamp)

    return MessageIngestion(
        message=message,
        conversation=conversation,
        created=created,
    )


def touch_last_message_at(conversation: Conversation, timestamp: datetime) -> None:

    if conversation.last_message_at is not None and conversation.last_message_at >= timestamp:
        return

    conversation.last_message_at = timestamp
    conversation.save(update_fields=["last_message_at", "updated_at"])


def reopen_if_closed(conversation: Conversation) -> bool:
    """Reabre um atendimento encerrado, descartando a thread antiga do provider.

    Zerar ``external_conversation_id`` é o que faz o próximo processamento abrir
    uma conversa nova no provider: o atendimento recomeça sem arrastar o
    histórico do anterior. Do lado de cá o corte sai de graça — a mensagem de
    encerramento é do assistente, então é ela que ``get_recent_messages`` toma
    como ponto de partida.
    """

    if conversation.status != ConversationStatus.CLOSED:
        return False

    conversation.status = ConversationStatus.ACTIVE
    conversation.external_conversation_id = None
    conversation.save(
        update_fields=["status", "external_conversation_id", "updated_at"],
    )

    return True


@transaction.atomic
def close_conversation(conversation: Conversation) -> bool:
    """Encerra o atendimento e avisa o cliente.

    O ``UPDATE`` condicionado a ``ACTIVE`` é o que garante a idempotência: duas
    execuções concorrentes do beat não geram duas mensagens de encerramento.
    """

    closed = Conversation.objects.filter(
        pk=conversation.pk,
        status=ConversationStatus.ACTIVE,
    ).update(status=ConversationStatus.CLOSED, updated_at=timezone.now())

    if not closed:
        return False

    conversation.status = ConversationStatus.CLOSED

    # Direto no model, e não por `register_message`: a mensagem de encerramento
    # não é atividade do atendimento, então não move `last_message_at`.
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content=CLOSING_MESSAGE,
        timestamp=timezone.now(),
    )

    return True


def close_inactive_conversations(*, idle_for: timedelta) -> int:
    """Encerra os atendimentos parados há mais de ``idle_for``.

    O relógio é o ``created_at`` das mensagens — quando a mensagem foi gravada —
    e não ``last_message_at``, que guarda o horário informado pelo provider. São
    coisas diferentes, e confundi-las encerra atendimento vivo: basta o provider
    repetir o mesmo timestamp para ``last_message_at`` ficar parado no passado
    enquanto o cliente conversa. ``updated_at`` da conversa também não serve,
    porque ``touch_last_message_at`` só grava quando o timestamp avança.

    A conversa que nunca recebeu mensagem entra pela própria data de criação:
    sem isso ela ficaria ativa para sempre, porque não há o que agregar.
    """

    cutoff = timezone.now() - idle_for
    stale = (
        Conversation.objects.filter(status=ConversationStatus.ACTIVE)
        .annotate(last_activity_at=Max("messages__created_at"))
        .filter(
            Q(last_activity_at__lt=cutoff)
            | Q(last_activity_at__isnull=True, created_at__lt=cutoff),
        )
    )

    return sum(close_conversation(conversation) for conversation in stale)


def ensure_external_conversation(conversation: Conversation) -> str | None:
    """Devolve a conversa do provider, criando-a na primeira vez que for preciso.

    A criação é uma chamada de rede, então mora aqui — chamada pela task — e não
    na ingestão: o webhook precisa responder rápido e não pode depender do
    provider estar de pé para aceitar a mensagem.
    """

    if conversation.external_conversation_id:
        return conversation.external_conversation_id

    external_conversation_id = asyncio.run(create_conversation())

    stored = Conversation.objects.filter(
        pk=conversation.pk,
        external_conversation_id__isnull=True,
    ).update(external_conversation_id=external_conversation_id)

    if not stored:
        conversation.refresh_from_db(fields=["external_conversation_id"])
    else:
        conversation.external_conversation_id = external_conversation_id

    return conversation.external_conversation_id


def has_newer_customer_message(*, conversation_id: int, message_id: int) -> bool:
    current_message = Message.objects.get(id=message_id)
    return Message.objects.filter(
        conversation_id=conversation_id,
        role=MessageRole.CUSTOMER,
        timestamp__gt=current_message.timestamp,
    ).exists()


def get_recent_messages(
    *,
    conversation_id: int,
) -> list[Message]:
    last_message = Message.objects.filter(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT
    ).order_by("-timestamp", "-id").first()

    if last_message:
        messages = Message.objects.filter(
            conversation_id=conversation_id,
            role=MessageRole.CUSTOMER,
            id__gt=last_message.pk
        ).order_by("timestamp", "id")
    else:
        messages = Message.objects.filter(
            conversation_id=conversation_id
        ).order_by("timestamp", "id")

    return list(messages)

def add_recommendations(*, conversation_id: int, property_codes: list[str]) -> None:
    conversation = Conversation.objects.get(id=conversation_id)
    conversation.recommended_properties.add(
        *Property.objects.filter(code__in=property_codes)
    )
