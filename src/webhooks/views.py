"""Camada de transporte do webhook de mensageria.

Responsabilidade desta view: validar o payload, delegar a persistência ao
serviço de conversas, agendar o processamento assíncrono e responder rápido.
Nenhuma regra de negócio e nenhuma chamada de IA acontecem aqui — o webhook
precisa devolver ``200`` antes do timeout do provedor.
"""

import logging

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from conversations.services import register_message
from conversations.tasks import schedule_conversation_processing
from webhooks.enums import WebhookEvent
from webhooks.serializers import MessageReceivedSerializer, WebhookEnvelopeSerializer

logger = logging.getLogger(__name__)


class MessageWebhookView(APIView):
    def post(self, request: Request) -> Response:
        envelope = WebhookEnvelopeSerializer(data=request.data)
        envelope.is_valid(raise_exception=True)

        if envelope.event_name != WebhookEvent.MESSAGE_RECEIVED:
            logger.info("Evento %s ignorado pelo webhook.", envelope.event_name)
            return self._ignored(envelope.raw_message_id)

        content = MessageReceivedSerializer(data=envelope.raw_content)
        content.is_valid(raise_exception=True)
        validated = content.validated_data
        message_id = validated["message_id"]
        ingestion = register_message(
            external_id=message_id,
            user_phone=validated["user_phone_number"],
            content=validated["message_content"],
            timestamp=validated["timestamp"],
        )

        if not ingestion.created:
            logger.info("Mensagem %s já registrada; reentrega ignorada.", message_id)
            return self._ignored(message_id)

        schedule_conversation_processing(
            conversation_id=ingestion.conversation.pk,
            trigger_message_id=ingestion.message.pk,
        )
        return Response(
            {"status": "accepted", "message_id": message_id},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _ignored(message_id: str | None) -> Response:
        return Response(
            {"status": "ignored", "message_id": message_id},
            status=status.HTTP_200_OK,
        )
