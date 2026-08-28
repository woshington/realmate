"""Contrato de entrada do webhook.

O payload chega de um sistema externo: nada pode ser assumido sobre ele. Estes
serializers são a fronteira entre o JSON não confiável e o domínio.

A validação do DRF para em ``validated_data``, que é um ``dict[str, Any]`` —
tipo que não ajuda ninguém abaixo da view. Por isso a fronteira não termina no
serializer: o que atravessa para o serviço é ``IncomingMessage``, um dataclass
imutável com ``UUID`` e ``datetime`` de verdade.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from rest_framework import serializers

from common.validators import PHONE_REGEX


class WebhookEnvelopeSerializer(serializers.Serializer):
    event = serializers.CharField()
    content = serializers.DictField(required=False, default=dict)

    @property
    def event_name(self) -> str:
        event: str = self.validated_data["event"]
        return event

    @property
    def raw_content(self) -> dict[str, Any]:
        content: dict[str, Any] = self.validated_data["content"]
        return content

    @property
    def raw_message_id(self) -> str | None:
        message_id = self.raw_content.get("message_id")
        return message_id if isinstance(message_id, str) else None


class MessageReceivedSerializer(serializers.Serializer):
    message_id = serializers.UUIDField()
    user_phone_number = serializers.RegexField(PHONE_REGEX)
    message_content = serializers.CharField(trim_whitespace=True, allow_blank=False)
    timestamp = serializers.DateTimeField()
