from celery import shared_task
from django.conf import settings

from assistant.client import get_assistant_client
from assistant.providers.enum import AIProvider
from conversations.models import Message


@shared_task
def reply_from_agent(message_id: str):
    current_message = Message.objects.get(external_id=message_id)
    conversation = current_message.conversation
    recent_message = Message.objects.filter(
        conversation_id=current_message.conversation_id
    ).order_by('-timestamp').first()

    if recent_message != current_message:
        return

    assistant = get_assistant_client(
        providers=AIProvider.OPENAI,
        model=settings.OPENAI_MODEL,
        timeout=settings.OPENAI_TIMEOUT,
    )
