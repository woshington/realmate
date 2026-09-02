from django.db.models import Prefetch, QuerySet
from rest_framework.generics import RetrieveAPIView, get_object_or_404

from conversations.models import Conversation, Message, PropertyRecommendation
from conversations.serializers import ConversationHistorySerializer




class ConversationMessagesView(RetrieveAPIView):
    serializer_class = ConversationHistorySerializer

    @staticmethod
    def normalize_phone(user_phone: str) -> str:
        return user_phone if user_phone.startswith("+") else f"+{user_phone}"

    def get_queryset(self) -> QuerySet[Conversation]:
        return (
            Conversation.objects.prefetch_related(
                Prefetch(
                    "messages",
                    queryset=Message.objects.order_by("timestamp"),
                ),
                Prefetch(
                    "recommendations",
                    queryset=PropertyRecommendation.objects.select_related("property"),
                ),
            )
        )

    def get_object(self) -> Conversation:
        return get_object_or_404(
            self.get_queryset(),
            user_phone=self.normalize_phone(self.kwargs["user_phone"]),
        )