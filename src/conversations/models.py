from django.db import models

from common.models import BaseModel
from conversations.enums import StatusConversationChoices, RoleMessageChoices


class Conversation(BaseModel):
    phone = models.CharField(unique=True, db_index=True)
    status = models.CharField(choices=StatusConversationChoices.choices, default="active")
    last_message_at = models.DateTimeField()

class Message(BaseModel):
    conversation = models.ForeignKey(Conversation, related_name="messages", on_delete=models.CASCADE)
    role = models.CharField(choices=RoleMessageChoices.choices)
    content = models.TextField()
    external_id = models.CharField(unique=True, null=True, db_index=True)
    timestamp = models.DateTimeField()

class RecommendedProperty(BaseModel):
    conversation = models.ForeignKey(Conversation, related_name="recommendations", on_delete=models.CASCADE)
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT)
    recommended_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ("conversation", "property")