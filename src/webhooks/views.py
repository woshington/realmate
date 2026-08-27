from rest_framework.response import Response
from rest_framework.views import APIView

from webhooks.serializers import EventSerializer
from webhooks.services import WebhookService


class MessageApiView(APIView):
    service = WebhookService()

    def post(self, request):
        serializer = EventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service_response = self.service.process(serializer)
        return Response(data=service_response, status=200)

