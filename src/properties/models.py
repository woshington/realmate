from typing import TYPE_CHECKING

from django.db import models

from common.models import TimestampedModel
from properties.enums import PropertySource, TransactionType

if TYPE_CHECKING:
    from conversations.models import Conversation, PropertyRecommendation


class Property(TimestampedModel):
    conversations: models.Manager["Conversation"]
    recommendations: models.Manager["PropertyRecommendation"]

    code = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="código do imóvel",
    )
    transaction_type = models.CharField(
        max_length=16,
        choices=TransactionType.choices,
        verbose_name="tipo de transação",
    )
    neighborhood = models.CharField(max_length=120, verbose_name="bairro")
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="preço",
    )
    bedrooms = models.PositiveSmallIntegerField(verbose_name="quartos")
    address = models.CharField(max_length=255, blank=True, verbose_name="endereço")
    description = models.TextField(blank=True, verbose_name="descrição")

    source = models.CharField(
        max_length=32,
        default=PropertySource.CSV,
        verbose_name="origem da carga",
    )

    imported_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Momento da última carga que tocou este registro.",
    )

    class Meta:
        verbose_name = "imóvel"
        verbose_name_plural = "imóveis"
        ordering = ["code"]
        indexes = [
            models.Index(
                fields=["transaction_type", "neighborhood", "price"],
                name="property_search_idx",
            ),
        ]

    def __str__(self) -> str:
        transaction = TransactionType(self.transaction_type).label
        return f"{self.code} — {self.neighborhood} ({transaction})"
