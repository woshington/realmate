import logging

from celery import shared_task

from config import settings
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.importers.schemas import ImportResult

logger = logging.getLogger(__name__)


@shared_task(name="properties.load_properties")
def load_properties() -> dict[str, int]:
    importers = [
        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH),
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH),
    ]

    total = ImportResult()

    for importer in importers:
        result = importer.load()
        logger.info(
            "%s: %d criados, %d atualizados, %d ignorados.",
            type(importer).__name__,
            result.created,
            result.updated,
            result.skipped,
        )
        for error in result.errors:
            logger.warning("%s: %s", type(importer).__name__, error)

        total.created += result.created
        total.updated += result.updated
        total.skipped += result.skipped
        total.errors.extend(result.errors)

    logger.info(
        "Carga de imóveis concluída: %d criados, %d atualizados, %d ignorados, %d erros.",
        total.created,
        total.updated,
        total.skipped,
        len(total.errors),
    )

    return {
        "created": total.created,
        "updated": total.updated,
        "skipped": total.skipped,
        "errors": len(total.errors),
    }