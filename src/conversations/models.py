import uuid
from django.db import models



from common.models import TimestampedModel
from common.validators import phone_validator
from conversations.enums import ConversationStatus, MessageRole
from properties.models import Property


class Conversation(TimestampedModel):
    messages: models.Manager["Message"]
    recommendations: models.Manager["PropertyRecommendation"]

    user_phone = models.CharField(
        max_length=16,
        unique=True,
        validators=[phone_validator],
        verbose_name="telefone do cliente",
    )
    status = models.CharField(
        max_length=16,
        choices=ConversationStatus.choices,
        default=ConversationStatus.ACTIVE,
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Última mensagem da conversa, seja do cliente ou do assistente.",
    )
    recommended_properties: "models.ManyToManyField[Property, PropertyRecommendation]" = models.ManyToManyField(
        Property,
        through="conversations.PropertyRecommendation",
        related_name="conversations",
        blank=True,
    )
    external_conversation_id = models.CharField(
        verbose_name="ID Externo da Conversa (Provider)",
        null=True
    )

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"


    def __str__(self) -> str:
        return self.user_phone

class Message(TimestampedModel):
    recommendations: models.Manager["PropertyRecommendation"]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    external_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="identificador externo",
    )
    role = models.CharField(max_length=16, choices=MessageRole.choices)
    content = models.TextField()
    timestamp = models.DateTimeField(
        help_text="Momento da mensagem informado pela origem, não o do INSERT.",
    )

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        indexes = [
            models.Index(
                fields=["conversation", "timestamp"],
                name="message_history_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.role}] {self.content[:40]}"


class PropertyRecommendation(TimestampedModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="recommendations",
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="recommendations",
    )

    class Meta:
        verbose_name = "imóvel recomendado"
        verbose_name_plural = "imóveis recomendados"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "property"],
                name="unique_property_per_conversation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.conversation.user_phone} → {self.property.code}"


