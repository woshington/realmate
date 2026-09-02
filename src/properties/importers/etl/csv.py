from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator

from properties.importers.etl.base import PropertyImporter
from properties.importers.etl.parsers import (
    parse_bedrooms,
    parse_price,
    parse_transaction_type,
    split_code_from_description,
)
from properties.importers.schemas import PropertyData
from properties.models import PropertySource


class CSVPropertyImporter(PropertyImporter):
    source = PropertySource.CSV

    _CODE_PATTERN = re.compile(r"codigo:\s*([A-Za-z0-9\-]+)", re.IGNORECASE)

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def extract(self) -> Iterator[dict[str, str]]:
        with self.file_path.open(newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)

    def transform(self, raw: dict[str, str]) -> PropertyData:
        code, description = split_code_from_description(
            raw["descricao"], self._CODE_PATTERN
        )

        return PropertyData(
            code=code,
            transaction_type=parse_transaction_type(raw["tipo_negocio"]),
            neighborhood=raw["bairro"].strip(),
            price=parse_price(raw["preco"]),
            bedrooms=parse_bedrooms(raw["quartos"]),
            address=raw["endereco"].strip(),
            description=description,
            source=self.source,
        )
