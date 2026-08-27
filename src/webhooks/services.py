import conversations
from conversations.models import Conversation, Message
from conversations.enums import RoleMessageChoices
from conversations.tasks import reply_from_agent
from webhooks.dtos import ResponseWebhookDTO
from webhooks.enums import EventEnum, ResponseEnum
from webhooks.serializers import EventSerializer


class WebhookService:
    @staticmethod
    def process(event: EventSerializer) -> ResponseWebhookDTO:
        if not event.event.lower() == EventEnum.MESSAGE_RECEIVED.value:
            return ResponseWebhookDTO(status=ResponseEnum.IGNORED, message=event.content.message_id)
        instance, _ = Conversation.objects.get_or_create(
            phone=event.content.user_phone_number,
            defaults={
                "status": "active",
                "last_message_at": event.content.timestamp,
            }
        )

        message, created = Message.objects.get_or_create(
            conversation_id=instance.id,
            external_id=event.content.message_id,
            defaults={
                "role": RoleMessageChoices.CUSTOMER.value,
                "content": event.content.message,
                "timestamp": event.content.timestamp,
            }
        )
        if not created:
            return ResponseWebhookDTO(status=ResponseEnum.IGNORED, message=event.content.message_id)

        instance.last_message_at = event.content.timestamp
        instance.save(update_fields=["last_message_at"])

        reply_from_agent.apply_async(
            args=(message.external_id,),
            countdown=10,
        )
        return ResponseWebhookDTO(status=ResponseEnum.ACCEPTED, message=event.content.message_id)