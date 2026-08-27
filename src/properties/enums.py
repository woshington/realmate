from django.db import models


class TransactionTypeChoices(models.TextChoices):
    RENT = "rent", "Aluguel"
    SALE = "sale", "Venda"


class SourceChoices(models.TextChoices):
    CSV = "csv", "CSV"
    XML = "xml", "XML"
    API = "api", "API"
