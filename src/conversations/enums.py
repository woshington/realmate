from django.db import models


class StatusConversationChoices(models.TextChoices):
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"


class RoleMessageChoices(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    ASSISTANT = "assistant", "Assistant"