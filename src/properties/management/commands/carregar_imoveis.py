"""``python src/manage.py carregar_imoveis``.

Execução manual da mesma carga que o Celery Beat roda diariamente. Existe para
o primeiro setup (popular o banco sem esperar o agendamento) e para reprocessar
uma fonte sob demanda.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from properties.models import Property
from properties.services import import_all_properties


class Command(BaseCommand):
    help = "Carrega os imóveis das fontes configuradas (CSV e JSON) no banco."

    def handle(self, *args: Any, **options: Any) -> None:
        result = import_all_properties()

        self.stdout.write(
            f"criados: {result.created} | "
            f"atualizados: {result.updated} | "
            f"ignorados: {result.skipped}"
        )

        for error in result.errors:
            self.stdout.write(self.style.WARNING(f"  {error}"))

        # Zero registro processado não é "sucesso silencioso": ou o arquivo
        # sumiu, ou está vazio, ou o formato mudou. Falhar aqui faz o problema
        # aparecer no exit code, e não só numa linha de log.
        if result.total_processed == 0:
            raise CommandError(
                "Nenhum registro processado — verifique os arquivos em DATA_DIR."
            )

        self.stdout.write(
            self.style.SUCCESS(f"Total de imóveis no banco: {Property.objects.count()}")
        )
