from datetime import UTC
from rest_framework import serializers

from conversations.models import Conversation, Message

ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


class MessageSerializer(serializers.ModelSerializer):
    timestamp = serializers.DateTimeField(format=ISO_UTC, default_timezone=UTC)

    class Meta:
        model = Message
        fields = ("role", "content", "timestamp")


class ConversationHistorySerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    properties_found = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ("user_phone", "properties_found", "messages")

    @staticmethod
    def get_properties_found(conversation: Conversation) -> list[str]:
        return [
            recommendation.property.code
            for recommendation in conversation.recommendations.all()
        ]
