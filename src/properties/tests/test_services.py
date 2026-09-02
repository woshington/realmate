"""Orquestração da carga e o ponto de extensão para novas fontes.

O desafio pede que XML e API REST entrem "com mudança mínima". Os testes de
extensão aqui provam isso de forma executável: uma fonte nova implementa duas
funções e herda upsert, contagem e tolerância a erro sem tocar em ``base.py``
nem nos importadores existentes.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from properties.enums import PropertySource, TransactionType
from properties.importers.etl.base import PropertyImporter
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.importers.schemas import PropertyData
from properties.models import Property
from properties.services import active_importers, import_all_properties
from properties.tasks import load_properties

pytestmark = pytest.mark.django_db

TOTAL_NAS_FONTES_REAIS = 20

WriteCsv = Callable[..., Path]
CsvRow = Callable[..., str]
WriteJson = Callable[..., Path]


class FakeAPIImporter(PropertyImporter):
    """Fonte fictícia: estender é implementar ``extract`` + ``transform``."""

    source = PropertySource.API

    def __init__(self, records: list[dict[str, Any]]):
        self.records = records

    def extract(self) -> Iterator[dict[str, Any]]:
        yield from self.records

    def transform(self, raw: dict[str, Any]) -> PropertyData:
        return PropertyData(
            code=raw["id"],
            transaction_type=TransactionType.SALE,
            neighborhood=raw["bairro"],
            price=Decimal(raw["valor"]),
            bedrooms=raw["dorms"],
            address="",
            description=raw.get("texto", ""),
            source=self.source,
        )


class BrokenImporter(PropertyImporter):
    """Fonte fora do ar: falha logo no ``extract``."""

    source = PropertySource.XML

    def extract(self) -> Iterator[Any]:
        raise OSError("arquivo do parceiro indisponível")

    def transform(self, raw: Any) -> PropertyData:  # pragma: no cover
        raise NotImplementedError


def api_record(code: str = "API-1", **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": code, "bairro": "Pina", "valor": "700000", "dorms": 3,
    }
    record.update(overrides)
    return record


class TestPontoDeExtensao:
    def test_fonte_nova_herda_o_upsert_sem_reimplementar_nada(self) -> None:
        result = FakeAPIImporter([api_record()]).load()

        imovel = Property.objects.get()
        assert result.created == 1
        assert imovel.code == "API-1"
        assert imovel.source == PropertySource.API
        assert imovel.imported_at is not None

    def test_fonte_nova_herda_a_idempotencia(self) -> None:
        importer = FakeAPIImporter([api_record()])

        importer.load()
        result = importer.load()

        assert Property.objects.count() == 1
        assert (result.created, result.updated) == (0, 1)

    def test_fonte_nova_herda_a_tolerancia_a_registro_ruim(self) -> None:
        result = FakeAPIImporter(
            [
                api_record("API-1"),
                {"id": "API-2", "bairro": "Pina"},  # faltam campos -> KeyError
                api_record("API-3", bairro="Boa Viagem", valor="500000", dorms=2),
            ]
        ).load()

        assert (result.created, result.skipped) == (2, 1)
        assert set(Property.objects.values_list("code", flat=True)) == {
            "API-1", "API-3",
        }

    def test_load_nao_pode_ser_sobrescrito_por_uma_fonte_nova(self) -> None:
        """``load`` é ``@final``: idempotência não é negociável por importador."""

        assert getattr(PropertyImporter.load, "__final__", False) is True


class TestConsolidacao:
    def test_os_importadores_ativos_sao_csv_e_json(self) -> None:
        fontes = {importer.source for importer in active_importers()}

        assert fontes == {PropertySource.CSV, PropertySource.JSON}

    def test_a_carga_completa_mescla_as_duas_fontes(self) -> None:
        result = import_all_properties()

        assert result.created == TOTAL_NAS_FONTES_REAIS
        assert Property.objects.count() == TOTAL_NAS_FONTES_REAIS

    def test_a_carga_completa_e_idempotente(self) -> None:
        import_all_properties()

        result = import_all_properties()

        assert (result.created, result.updated) == (0, TOTAL_NAS_FONTES_REAIS)
        assert Property.objects.count() == TOTAL_NAS_FONTES_REAIS

    def test_soma_os_numeros_de_todas_as_fontes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "properties.services.active_importers",
            lambda: [FakeAPIImporter([api_record("API-1")]),
                     FakeAPIImporter([api_record("API-2")])],
        )

        result = import_all_properties()

        assert result.created == 2
        assert result.total_processed == 2


class TestFonteQueFalha:
    """Uma fonte fora do ar não pode derrubar a carga das outras."""

    def test_a_falha_de_uma_fonte_nao_impede_as_demais(
        self, monkeypatch: pytest.MonkeyPatch,
        write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        csv_path = write_csv(csv_row(descricao="Apto. codigo:OK-1"))
        monkeypatch.setattr(
            "properties.services.active_importers",
            lambda: [BrokenImporter(), CSVPropertyImporter(csv_path)],
        )

        result = import_all_properties()

        assert result.created == 1
        assert Property.objects.get().code == "OK-1"

    def test_a_falha_da_fonte_aparece_no_resultado(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "properties.services.active_importers", lambda: [BrokenImporter()],
        )

        result = import_all_properties()

        assert any("indisponível" in error for error in result.errors)

    def test_arquivo_inexistente_e_tratado_como_falha_de_fonte(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "properties.services.active_importers",
            lambda: [CSVPropertyImporter(tmp_path / "nao-existe.csv")],
        )

        result = import_all_properties()

        assert result.total_processed == 0
        assert len(result.errors) == 1

    def test_erros_de_registro_aparecem_no_resultado_consolidado(
        self, monkeypatch: pytest.MonkeyPatch, write_json: WriteJson,
    ) -> None:
        json_path = write_json(
            [{"tipo_negocio": "aluguel", "descricao": "sem código"}]
        )
        monkeypatch.setattr(
            "properties.services.active_importers",
            lambda: [JSONPropertyImporter(json_path)],
        )

        result = import_all_properties()

        assert result.skipped == 1
        assert len(result.errors) == 1


class TestTaskDeCarga:
    def test_devolve_o_resumo_serializavel_da_carga(self) -> None:
        resumo = load_properties()

        assert resumo == {
            "created": TOTAL_NAS_FONTES_REAIS, "updated": 0, "skipped": 0, "errors": 0,
        }
        assert json.dumps(resumo)  # precisa atravessar o result backend do Celery

    def test_roda_a_mesma_carga_da_orquestracao(self) -> None:
        load_properties()

        assert Property.objects.count() == TOTAL_NAS_FONTES_REAIS

    def test_a_execucao_diaria_e_idempotente(self) -> None:
        load_properties()

        resumo = load_properties()

        assert resumo["created"] == 0
        assert resumo["updated"] == TOTAL_NAS_FONTES_REAIS
        assert Property.objects.count() == TOTAL_NAS_FONTES_REAIS

    def test_conta_os_erros_em_vez_de_propaga_los(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A task não pode estourar: o Beat reagendaria a carga inteira."""

        monkeypatch.setattr(
            "properties.services.active_importers", lambda: [BrokenImporter()],
        )

        resumo = load_properties()

        assert resumo == {"created": 0, "updated": 0, "skipped": 0, "errors": 1}
