"""Guardas de configuração do Celery."""

from django.conf import settings

from config.celery import app


def test_toda_task_agendada_esta_registrada_no_worker() -> None:
    """Regressão: o beat publicava `properties.tasks.load_properties`, mas o
    `@shared_task` registra `properties.load_properties` — o worker descartava
    a mensagem com `Received unregistered task`.
    """
    app.autodiscover_tasks(force=True)

    agendadas = {entrada["task"] for entrada in settings.CELERY_BEAT_SCHEDULE.values()}

    assert agendadas <= set(app.tasks)
