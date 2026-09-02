from django.db import models


class ConversationStatus(models.TextChoices):
    ACTIVE = "active", "Ativa"
    CLOSED = "closed", "Encerrada"


class MessageRole(models.TextChoices):
    CUSTOMER = "customer", "Cliente"
    ASSISTANT = "assistant", "Assistente"
