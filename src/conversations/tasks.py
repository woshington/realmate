import logging
import uuid
from datetime import timedelta

from agents import Runner
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from assistant import FALLBACK_MESSAGE, agent
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps
from assistant.helpers import to_model_messages
from conversations.enums import MessageRole
from conversations.models import Message, Conversation
from conversations.services import (
    add_recommendations,
    close_inactive_conversations,
    ensure_external_conversation,
    get_recent_messages,
    has_newer_customer_message,
    register_message,
)

logger = logging.getLogger(__name__)


def schedule_conversation_processing(
    *,
    conversation_id: int,
    trigger_message_id: int,
) -> None:
    process_conversation.apply_async(
        kwargs={
            "conversation_id": conversation_id,
            "trigger_message_id": trigger_message_id,
        },
        countdown=settings.DEBOUNCE_WINDOW_SECONDS,
    )


@shared_task(name="conversations.process_conversation")
def process_conversation(
    conversation_id: int,
    trigger_message_id: int,
) -> None:
    lock_id = f"lock:{conversation_id}-{trigger_message_id}"

    if not cache.add(lock_id, "true", 15):
        logger.warning(
            "Task process_conversation para o recurso %s "
            "já está em execução. Pulando.",
            lock_id,
        )
        return

    try:
        if has_newer_customer_message(
            conversation_id=conversation_id,
            message_id=trigger_message_id,
        ):
            logger.info(
                "Processamento da conversa %s disparado pela mensagem %s "
                "foi superado por uma mensagem mais recente.",
                conversation_id,
                trigger_message_id,
            )
            return

        logger.info(
            "Conversa %s pronta para processamento (mensagem %s).",
            conversation_id,
            trigger_message_id,
        )

        trigger = Message.objects.get(pk=trigger_message_id)

        deps = AssistantDeps(conversation_id=conversation_id)

        user_agent = agent.get_agent()

        conversation = Conversation.objects.get(pk=conversation_id)

        history = get_recent_messages(conversation_id=conversation_id)

        try:
            result = Runner.run_sync(
                starting_agent=user_agent,
                input=[
                    *to_model_messages(history),
                ],
                context=deps,
                conversation_id=ensure_external_conversation(conversation),
            )
        except Exception:
            logger.exception(
                "Erro ao processar conversa %s; "
                "respondendo com mensagem de fallback.",
                conversation_id,
            )
            reply = AgentReply(
                message=FALLBACK_MESSAGE,
            )

        else:
            reply = result.final_output

        logger.info(
            "Conversa %s: resposta da IA obtida com %s imóvel(is) "
            "recomendado(s): %s",
            conversation_id,
            len(reply.recommended_properties),
            [
                recommended.code
                for recommended in reply.recommended_properties
            ],
        )

        register_message(
            external_id=uuid.uuid4(),
            user_phone=trigger.conversation.user_phone,
            content=reply.message,
            timestamp=trigger.timestamp,
            role=MessageRole.ASSISTANT,
        )

        recommended_codes = [
            recommended.code
            for recommended in reply.recommended_properties
        ] or deps.presented_codes

        add_recommendations(
            conversation_id=conversation_id,
            property_codes=recommended_codes,
        )

    finally:
        cache.delete(lock_id)


@shared_task(name="conversations.expire_inactive_conversations")
def expire_inactive_conversations() -> int:
    """Varredura periódica que encerra os atendimentos abandonados.

    O cliente que some não deixa a conversa aberta para sempre; e a próxima
    mensagem dele reabre a conversa numa thread nova do provider, começando um
    atendimento limpo. O retorno é um `int` porque precisa ser serializável em
    JSON para o result backend do Celery.
    """

    closed = close_inactive_conversations(
        idle_for=timedelta(hours=settings.INACTIVITY_TIMEOUT_HOURS),
    )

    if closed:
        logger.info("%s conversa(s) encerrada(s) por inatividade.", closed)

    return closed
