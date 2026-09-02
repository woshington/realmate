from django.urls import re_path

from conversations.views import ConversationMessagesView

app_name = "conversations"

urlpatterns = [
    # O telefone é validado no próprio path: um formato inesperado não chega à
    # view (404 de rota), e o `+` continua legível na URL.
    re_path(
        r"^conversations/(?P<user_phone>\+?\d{12,13})/messages$",
        ConversationMessagesView.as_view(),
        name="messages",
    ),
]
