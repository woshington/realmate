from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from properties.importers.etl.base import PropertyImporter
from properties.importers.etl.parsers import (
    parse_bedrooms,
    parse_price,
    parse_transaction_type,
    split_code_from_description,
)
from properties.importers.schemas import PropertyData
from properties.models import PropertySource


class JSONPropertyImporter(PropertyImporter):
    source = PropertySource.JSON

    _CODE_PATTERN = re.compile(r"ref:\s*([A-Za-z0-9\-]+)", re.IGNORECASE)

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def extract(self) -> Iterator[dict[str, Any]]:
        with self.file_path.open(encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, list):
            raise ValueError(
                f"{self.file_path}: esperado uma lista de imóveis, "
                f"recebido {type(payload).__name__}."
            )

        for raw in payload:
            if not isinstance(raw, dict):
                raise ValueError(
                    f"{self.file_path}: esperado objetos na lista, "
                    f"recebido {type(raw).__name__}."
                )
            yield raw

    def transform(self, raw: dict[str, Any]) -> PropertyData:
        code, description = split_code_from_description(
            str(raw["descricao"]), self._CODE_PATTERN
        )

        return PropertyData(
            code=code,
            transaction_type=parse_transaction_type(raw["tipo_negocio"]),
            neighborhood=str(raw["bairro"]).strip(),
            price=parse_price(raw["preco"]),
            bedrooms=parse_bedrooms(raw["quartos"]),
            address=str(raw["endereco"]).strip(),
            description=description,
            source=self.source,
        )
