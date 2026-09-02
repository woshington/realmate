import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction

from assistant.agent import create_conversation
from conversations.enums import MessageRole
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
