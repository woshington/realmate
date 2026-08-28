"""Registro das estratégias de carga.

Este módulo é o único ponto que a aplicação precisa tocar para ganhar uma
origem nova: escreva a estratégia, acrescente-a aqui, e tanto o comando de
gerenciamento quanto a task diária passam a carregá-la.
"""

from django.conf import settings

from properties.importers.base import (
    InvalidPropertyRecord,
    PropertyData,
    PropertyImporter,
    TabularPropertyImporter,
)
from properties.importers.files import (
    CsvPropertyImporter,
    FilePropertyImporter,
    JsonPropertyImporter,
)

__all__ = [
    "CsvPropertyImporter",
    "FilePropertyImporter",
    "InvalidPropertyRecord",
    "JsonPropertyImporter",
    "PropertyData",
    "PropertyImporter",
    "TabularPropertyImporter",
    "default_importers",
]


def default_importers() -> list[PropertyImporter]:
    """Origens carregadas por padrão, na ordem em que devem ser processadas."""

    return [
        CsvPropertyImporter(settings.PROPERTIES_CSV_PATH),
        JsonPropertyImporter(settings.PROPERTIES_JSON_PATH),
    ]
