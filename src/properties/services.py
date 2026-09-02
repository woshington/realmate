"""Orquestração da carga de imóveis.

Ponto único onde a lista de fontes ativas é declarada e onde os resultados
parciais são consolidados. O agendamento diário (task do Celery) e a execução
manual (management command) chamam esta mesma função: qual fonte entra na carga
não pode divergir entre os dois caminhos.

Registrar uma fonte nova é acrescentar uma linha em ``active_importers`` — o
resto do fluxo (upsert idempotente, contagem, coleta de erros) é herdado de
``PropertyImporter.load``.
"""

import logging

from django.conf import settings

from properties.importers.etl.base import PropertyImporter
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.importers.schemas import ImportResult

logger = logging.getLogger(__name__)


def active_importers() -> list[PropertyImporter]:
    """Fontes que participam da carga."""

    return [
        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH),
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH),
    ]


def import_all_properties() -> ImportResult:
    """Executa todas as fontes e devolve o resultado consolidado.

    Uma fonte que falha por completo não impede as outras de rodar: o erro é
    registrado no resultado e a carga segue. Isso importa porque as fontes são
    independentes — um XML de parceiro fora do ar não pode derrubar a carga do
    CSV interno.
    """

    total = ImportResult()

    for importer in active_importers():
        name = type(importer).__name__
        try:
            result = importer.load()
        except (OSError, ValueError) as error:
            logger.exception("%s: carga interrompida (%s).", name, error)
            total.errors.append(f"{name}: {error}")
            continue

        logger.info(
            "%s: %d criados, %d atualizados, %d ignorados.",
            name,
            result.created,
            result.updated,
            result.skipped,
        )
        for error_message in result.errors:
            logger.warning("%s: %s", name, error_message)

        total.created += result.created
        total.updated += result.updated
        total.skipped += result.skipped
        total.errors.extend(result.errors)

    logger.info(
        "Carga de imóveis concluída: %d criados, %d atualizados, %d ignorados, "
        "%d erros.",
        total.created,
        total.updated,
        total.skipped,
        len(total.errors),
    )

    return total
