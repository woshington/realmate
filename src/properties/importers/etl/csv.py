from __future__ import annotations

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from properties.importers.etl.base import PropertyImporter
from properties.importers.schemas import PropertyData
from properties.models import PropertySource, TransactionType


class CSVPropertyImporter(PropertyImporter):
    source = PropertySource.CSV

    _CODE_PATTERN = re.compile(r"codigo:\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
    _TRANSACTION_MAP = {
        "aluguel": TransactionType.RENT,
        "venda": TransactionType.SALE,
    }

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def extract(self) -> Iterator[dict[str, str]]:
        with self.file_path.open(newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)

    def transform(self, raw: dict[str, str]) -> PropertyData:
        code, description = self._split_code_from_description(raw["descricao"])

        transaction_type = self._TRANSACTION_MAP.get(
            raw["tipo_negocio"].strip().lower()
        )
        if transaction_type is None:
            raise ValueError(f"tipo_negocio desconhecido: {raw['tipo_negocio']!r}")

        try:
            price = Decimal(raw["preco"].strip())
        except InvalidOperation as exc:
            raise ValueError(f"preço inválido: {raw['preco']!r}") from exc

        return PropertyData(
            code=code,
            transaction_type=transaction_type,
            neighborhood=raw["bairro"].strip(),
            price=price,
            bedrooms=int(raw["quartos"].strip()),
            address=raw["endereco"].strip(),
            description=description,
            source=self.source,
            source_reference=str(self.file_path),
        )

    def _split_code_from_description(self, description: str) -> tuple[str, str]:
        match = self._CODE_PATTERN.search(description)
        if not match:
            raise ValueError(f"código não encontrado na descrição: {description!r}")

        code = match.group(1).strip().upper()
        clean_description = self._CODE_PATTERN.sub("", description).strip().rstrip(". ")
        return code, clean_description