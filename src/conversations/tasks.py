import logging

from celery import shared_task
from django.conf import settings

from assistant import agent
from assistant.history import to_model_messages
from conversations.enums import MessageRole
from conversations.models import Message
from conversations.services import (
    get_recent_messages,
    has_newer_customer_message, register_customer_message, add_recommendations,
)

logger = logging.getLogger(__name__)


def schedule_conversation_processing(
    *, conversation_id: int, trigger_message_id: int
) -> None:
    process_conversation.apply_async(
        kwargs={
            "conversation_id": conversation_id,
            "trigger_message_id": trigger_message_id,
        },
        countdown=settings.DEBOUNCE_WINDOW_SECONDS,
    )


@shared_task(name="conversations.process_conversation")
def process_conversation(conversation_id: int, trigger_message_id: int) -> None:
    if has_newer_customer_message(
        conversation_id=conversation_id, message_id=trigger_message_id
    ):
        logger.info(
            "Processamento da conversa %s disparado pela mensagem %s foi "
            "superado por uma mensagem mais recente.",
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
    history = get_recent_messages(
        conversation_id=conversation_id,
        limit=settings.AGENT_HISTORY_MESSAGE_LIMIT,
        before_message_id=trigger_message_id,
    )

    user_agent = agent.get_agent()
    result = user_agent.run_sync(
        trigger.content,
        message_history=to_model_messages(history),
        deps=str(conversation_id),
    )
    reply = result.output

    logger.info(
        "Conversa %s: resposta da IA obtida com %s imóvel(is) recomendado(s): %s",
        conversation_id,
        len(reply.recommended_properties),
        [recommended.code for recommended in reply.recommended_properties],
    )

    register_customer_message(
        external_id=trigger.external_id,
        user_phone=trigger.conversation.user_phone,
        content=reply.message,
        timestamp=trigger.timestamp,
        role=MessageRole.ASSISTANT,
    )

    add_recommendations(
        conversation_id=conversation_id,
        property_codes=[recommended.code for recommended in reply.recommended_properties],
    )
