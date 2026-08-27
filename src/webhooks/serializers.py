from rest_framework import serializers
from common.helpers import PHONE_REGEX


class ContentEventSerializer(serializers.Serializer):
    message_id = serializers.CharField()
    user_phone_number = serializers.CharField()
    message_content = serializers.CharField()
    timestamp = serializers.DateTimeField()

    @staticmethod
    def validate_user_phone_number(value: str) -> str:
        if not PHONE_REGEX.fullmatch(value):
            raise serializers.ValidationError(
                "user_phone_number deve seguir o formato +DDI+DDD+numero, ex: +5588999999999."
            )
        return value

class EventSerializer(serializers.Serializer):
    event = serializers.CharField()
    content =  ContentEventSerializer()