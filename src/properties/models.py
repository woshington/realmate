from django.db import models

from common.models import BaseModel
from properties.enums import TransactionTypeChoices, SourceChoices


class Property(BaseModel):
    code = models.CharField(max_length=50, unique=True, db_index=True)
    transaction_type = models.CharField(choices=TransactionTypeChoices.choices)
    neighborhood = models.CharField(db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    bedrooms = models.PositiveSmallIntegerField(null=True)
    description = models.TextField()
    source = models.CharField(choices=SourceChoices.choices)
    raw_payload = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
