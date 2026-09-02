import logging

from celery import shared_task

from properties.services import import_all_properties

logger = logging.getLogger(__name__)


@shared_task(name="properties.load_properties")
def load_properties() -> dict[str, int]:
    """Carga diária dos imóveis.

    A task é só o agendamento: a orquestração vive em
    ``properties.services.import_all_properties``, compartilhada com o
    management command. O retorno é um dict simples porque precisa ser
    serializável em JSON para o result backend do Celery.
    """

    result = import_all_properties()

    return {
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "errors": len(result.errors),
    }
