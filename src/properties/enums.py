from django.db import models


class TransactionType(models.TextChoices):
    RENT = "aluguel", "Aluguel"
    SALE = "venda", "Venda"


class PropertySource(models.TextChoices):
    CSV = "csv", "Arquivo CSV"
    JSON = "json", "Arquivo JSON"
    API = "api", "API REST"
    XML = "xml", "Arquivo XML"
