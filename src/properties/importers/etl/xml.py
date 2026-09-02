from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from properties.importers.etl.base import PropertyImporter
from properties.importers.schemas import PropertyData
from properties.models import PropertySource


class XMLPropertyImporter(PropertyImporter):
    source = PropertySource.XML

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def extract(self) -> Iterator[Any]:
        raise NotImplementedError("Importador de XML ainda não implementado.")

    def transform(self, raw: Any) -> PropertyData:
        raise NotImplementedError("Importador de XML ainda não implementado.")