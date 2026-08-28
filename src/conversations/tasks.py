import logging
import uuid

from celery import shared_task
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits
from django.conf import settings

from assistant import FALLBACK_MESSAGE, agent
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps
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
    try:
        result = user_agent.run_sync(
            trigger.content,
            message_history=to_model_messages(history),
            deps=AssistantDeps(conversation_id=conversation_id),
            usage_limits=UsageLimits(request_limit=settings.AGENT_REQUEST_LIMIT),
        )
    except UsageLimitExceeded:
        logger.warning(
            "Conversa %s: agente atingiu o limite de %s requisições sem "
            "concluir; respondendo com a mensagem de fallback.",
            conversation_id,
            settings.AGENT_REQUEST_LIMIT,
        )
        reply = AgentReply(message=FALLBACK_MESSAGE)
    else:
        reply = result.output

    logger.info(
        "Conversa %s: resposta da IA obtida com %s imóvel(is) recomendado(s): %s",
        conversation_id,
        len(reply.recommended_properties),
        [recommended.code for recommended in reply.recommended_properties],
    )

    register_customer_message(
        external_id=str(uuid.uuid4()),
        user_phone=trigger.conversation.user_phone,
        content=reply.message,
        timestamp=trigger.timestamp,
        role=MessageRole.ASSISTANT,
    )

    add_recommendations(
        conversation_id=conversation_id,
        property_codes=[recommended.code for recommended in reply.recommended_properties],
    )
