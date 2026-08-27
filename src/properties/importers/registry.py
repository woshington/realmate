from pathlib import Path

from properties.importers import BaseImporter
from properties.importers import CSVImporter, JSONImporter, APIImporter

SOURCES: list[BaseImporter] = [
    CSVImporter(Path("data/imoveis.csv")),
    JSONImporter(Path("data/imoveis_resumo.json")),
    APIImporter(),
]