"""Orquestração da carga e o ponto de extensão para novas fontes.

O desafio pede que XML e API REST entrem "com mudança mínima". Os testes de
extensão aqui existem para provar isso de forma executável: um importador novo
implementa duas funções e herda upsert, contagem e tolerância a erro sem tocar
em `base.py` nem nos importadores existentes.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest

from properties.enums import PropertySource, TransactionType
from properties.importers.etl.base import PropertyImporter
from properties.importers.schemas import PropertyData
from properties.models import Property
from properties.services import active_importers, import_all_properties
from properties.tasks import load_properties

pytestmark = pytest.mark.django_db


class FakeAPIImporter(PropertyImporter):
    """Fonte fictícia: prova que estender é implementar extract + transform."""

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
            source_reference="https://parceiro.example/imoveis",
        )


class BrokenImporter(PropertyImporter):
    source = PropertySource.XML

    def extract(self) -> Iterator[Any]:
        raise OSError("arquivo do parceiro indisponível")

    def transform(self, raw: Any) -> PropertyData:  # pragma: no cover
        raise NotImplementedError


# --- Ponto de extensão ------------------------------------------------------


def test_fonte_nova_herda_o_upsert_sem_reimplementar_nada() -> None:
    importer = FakeAPIImporter(
        [{"id": "API-1", "bairro": "Pina", "valor": "700000", "dorms": 3}]
    )

    result = importer.load()

    imovel = Property.objects.get()
    assert result.created == 1
    assert imovel.code == "API-1"
    assert imovel.source == PropertySource.API
    assert imovel.imported_at is not None


def test_fonte_nova_herda_a_idempotencia() -> None:
    importer = FakeAPIImporter(
        [{"id": "API-1", "bairro": "Pina", "valor": "700000", "dorms": 3}]
    )

    importer.load()
    result = importer.load()

    assert Property.objects.count() == 1
    assert (result.created, result.updated) == (0, 1)


def test_fonte_nova_herda_a_tolerancia_a_registro_ruim() -> None:
    importer = FakeAPIImporter(
        [
            {"id": "API-1", "bairro": "Pina", "valor": "700000", "dorms": 3},
            {"id": "API-2", "bairro": "Pina"},  # faltam campos -> KeyError
            {"id": "API-3", "bairro": "Boa Viagem", "valor": "500000", "dorms": 2},
        ]
    )

    result = importer.load()

    assert (result.created, result.skipped) == (2, 1)
    assert set(Property.objects.values_list("code", flat=True)) == {"API-1", "API-3"}


def test_load_nao_pode_ser_sobrescrito_por_uma_fonte_nova() -> None:
    """`load` é @final: a regra de idempotência não é negociável por importador."""

    assert getattr(PropertyImporter.load, "__final__", False) is True


# --- Consolidação -----------------------------------------------------------


def test_importadores_ativos_sao_csv_e_json() -> None:
    fontes = {importer.source for importer in active_importers()}

    assert fontes == {PropertySource.CSV, PropertySource.JSON}


def test_carga_completa_mescla_as_duas_fontes() -> None:
    result = import_all_properties()

    assert result.created == 20
    assert Property.objects.count() == 20


def test_carga_completa_e_idempotente() -> None:
    import_all_properties()

    result = import_all_properties()

    assert (result.created, result.updated) == (0, 20)
    assert Property.objects.count() == 20


def test_fonte_que_falha_por_completo_nao_impede_as_outras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Um XML de parceiro fora do ar não pode derrubar a carga do CSV interno."""

    csv_path = tmp_path / "imoveis.csv"
    csv_path.write_text(
        "tipo_negocio,preco,quartos,bairro,endereco,descricao\n"
        'aluguel,2500,2,Boa Viagem,"Rua X, 1","Apto. codigo:OK-1"\n',
        encoding="utf-8",
    )

    from properties.importers.etl.csv import CSVPropertyImporter

    monkeypatch.setattr(
        "properties.services.active_importers",
        lambda: [BrokenImporter(), CSVPropertyImporter(csv_path)],
    )

    result = import_all_properties()

    assert result.created == 1
    assert Property.objects.get().code == "OK-1"
    assert any("indisponível" in error for error in result.errors)


def test_erros_de_registro_aparecem_no_resultado_consolidado(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    json_path = tmp_path / "imoveis.json"
    json_path.write_text(
        json.dumps([{"tipo_negocio": "aluguel", "descricao": "sem código"}]),
        encoding="utf-8",
    )

    from properties.importers.etl.json import JSONPropertyImporter

    monkeypatch.setattr(
        "properties.services.active_importers",
        lambda: [JSONPropertyImporter(json_path)],
    )

    result = import_all_properties()

    assert result.skipped == 1
    assert len(result.errors) == 1


# --- Task -------------------------------------------------------------------


def test_task_devolve_o_resumo_serializavel_da_carga() -> None:
    resumo = load_properties()

    assert resumo == {"created": 20, "updated": 0, "skipped": 0, "errors": 0}
    assert json.dumps(resumo)  # precisa atravessar o result backend do Celery


def test_task_roda_a_mesma_carga_do_command() -> None:
    load_properties()

    assert Property.objects.count() == 20
