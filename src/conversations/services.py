
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction

from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from properties.models import Property


@dataclass(frozen=True, slots=True)
class MessageIngestion:
    message: Message
    conversation: Conversation
    created: bool


@transaction.atomic
def register_customer_message(
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
    limit: int,
    before_message_id: int | None = None,
) -> list[Message]:
    queryset = Message.objects.filter(conversation_id=conversation_id)
    if before_message_id is not None:
        queryset = queryset.filter(id__lt=before_message_id)

    newest_first = queryset.order_by("-timestamp", "-id")[:limit]
    return list(reversed(newest_first))

def add_recommendations(*, conversation_id: int, property_codes: list[str]) -> None:
    conversation = Conversation.objects.get(id=conversation_id)
    conversation.recommended_properties.add(
        *Property.objects.filter(code__in=property_codes)
    )