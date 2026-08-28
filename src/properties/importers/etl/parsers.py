import re
from decimal import Decimal, InvalidOperation
from typing import Any

from properties.models import TransactionType

_TRANSACTION_MAP = {
    "aluguel": TransactionType.RENT,
    "venda": TransactionType.SALE,
}


def parse_transaction_type(value: str) -> str:
    transaction_type = _TRANSACTION_MAP.get(str(value).strip().lower())
    if transaction_type is None:
        raise ValueError(f"tipo_negocio desconhecido: {value!r}")
    return transaction_type


def parse_price(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"preço inválido: {value!r}") from exc


def parse_bedrooms(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"quantidade de quartos inválida: {value!r}") from exc


def split_code_from_description(
    description: str, pattern: re.Pattern[str]
) -> tuple[str, str]:
    match = pattern.search(description)
    if not match:
        raise ValueError(f"código não encontrado na descrição: {description!r}")

    code = match.group(1).strip().upper()
    clean_description = pattern.sub("", description).strip().rstrip(". ")
    return code, clean_description
