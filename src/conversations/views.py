"""API de leitura do histórico de conversas.

Somente leitura e sem regra de negócio: a view resolve a conversa pelo telefone
e o serializer monta o payload. O ``prefetch_related`` existe para que o
histórico inteiro saia em poucas queries, independentemente do tamanho da
conversa.
"""

from django.db.models import Prefetch, QuerySet
from rest_framework.generics import RetrieveAPIView, get_object_or_404

from conversations.models import Conversation, Message, PropertyRecommendation
from conversations.serializers import ConversationHistorySerializer


def history_queryset() -> QuerySet[Conversation]:
    """Conversa com histórico e recomendações já ordenados.

    A ordenação é explícita aqui, e não herdada do ``Meta.ordering`` dos
    modelos, porque ela é parte do contrato da API — que é verificado por
    testes automatizados. Deixá-la no query site garante que uma mudança no
    default de ordenação de um modelo não quebre a resposta em silêncio.
    """

    return Conversation.objects.prefetch_related(
        Prefetch(
            "messages",
            queryset=Message.objects.order_by("timestamp", "id"),
        ),
        Prefetch(
            "recommendations",
            queryset=PropertyRecommendation.objects.select_related("property").order_by(
                "created_at", "id"
            ),
        ),
    )


class ConversationMessagesView(RetrieveAPIView):
    """``GET /api/conversations/{user_phone}/messages``."""

    serializer_class = ConversationHistorySerializer

    def get_object(self) -> Conversation:
        return get_object_or_404(
            history_queryset(),
            user_phone=normalize_phone(self.kwargs["user_phone"]),
        )


def normalize_phone(user_phone: str) -> str:
    """Aceita o telefone com ou sem o ``+`` na URL.

    O ``+`` é literal num path, mas alguns clientes o omitem ao montar a URL.
    Como o telefone é a chave da conversa, normalizar aqui evita um 404
    enganoso por um detalhe de encoding.
    """

    return user_phone if user_phone.startswith("+") else f"+{user_phone}"
