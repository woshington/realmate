from __future__ import annotations

from typing import Any, Iterator

from properties.importers.etl.base import PropertyImporter
from properties.importers.schemas import PropertyData
from properties.models import PropertySource


class APIRestPropertyImporter(PropertyImporter):
    source = PropertySource.API

    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key

    def extract(self) -> Iterator[dict[str, Any]]:
        raise NotImplementedError("Importador de API REST ainda não implementado.")

    def transform(self, raw: dict[str, Any]) -> PropertyData:
        raise NotImplementedError("Importador de API REST ainda não implementado.")