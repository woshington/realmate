"""Load orchestration and the extension point for new sources.

The challenge asks for XML and a REST API to fit in "with minimal change". The
extension tests here prove that in an executable way: a new source implements
two functions and inherits upsert, counting and error tolerance without touching
``base.py`` nor the existing importers.
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

TOTAL_IN_THE_REAL_SOURCES = 20

WriteCsv = Callable[..., Path]
CsvRow = Callable[..., str]
WriteJson = Callable[..., Path]


class FakeAPIImporter(PropertyImporter):
    """Fictional source: extending means implementing ``extract`` + ``transform``."""

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
    """Source that is down: it fails right at ``extract``."""

    source = PropertySource.XML

    def extract(self) -> Iterator[Any]:
        raise OSError("partner file unavailable")

    def transform(self, raw: Any) -> PropertyData:  # pragma: no cover
        raise NotImplementedError


def api_record(code: str = "API-1", **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": code, "bairro": "Pina", "valor": "700000", "dorms": 3,
    }
    record.update(overrides)
    return record


class TestExtensionPoint:
    def test_a_new_source_inherits_the_upsert_without_reimplementing_anything(self) -> None:
        result = FakeAPIImporter([api_record()]).load()

        property_ = Property.objects.get()
        assert result.created == 1
        assert property_.code == "API-1"
        assert property_.source == PropertySource.API
        assert property_.imported_at is not None

    def test_a_new_source_inherits_idempotence(self) -> None:
        importer = FakeAPIImporter([api_record()])

        importer.load()
        result = importer.load()

        assert Property.objects.count() == 1
        assert (result.created, result.updated) == (0, 1)

    def test_a_new_source_inherits_tolerance_to_a_bad_record(self) -> None:
        result = FakeAPIImporter(
            [
                api_record("API-1"),
                {"id": "API-2", "bairro": "Pina"},  # missing fields -> KeyError
                api_record("API-3", bairro="Boa Viagem", valor="500000", dorms=2),
            ]
        ).load()

        assert (result.created, result.skipped) == (2, 1)
        assert set(Property.objects.values_list("code", flat=True)) == {
            "API-1", "API-3",
        }

    def test_load_cannot_be_overridden_by_a_new_source(self) -> None:
        """``load`` is ``@final``: idempotence is not negotiable per importer."""

        assert getattr(PropertyImporter.load, "__final__", False) is True


class TestConsolidation:
    def test_the_active_importers_are_csv_and_json(self) -> None:
        sources = {importer.source for importer in active_importers()}

        assert sources == {PropertySource.CSV, PropertySource.JSON}

    def test_the_full_load_merges_both_sources(self) -> None:
        result = import_all_properties()

        assert result.created == TOTAL_IN_THE_REAL_SOURCES
        assert Property.objects.count() == TOTAL_IN_THE_REAL_SOURCES

    def test_the_full_load_is_idempotent(self) -> None:
        import_all_properties()

        result = import_all_properties()

        assert (result.created, result.updated) == (0, TOTAL_IN_THE_REAL_SOURCES)
        assert Property.objects.count() == TOTAL_IN_THE_REAL_SOURCES

    def test_sums_the_numbers_of_every_source(
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


class TestFailingSource:
    """A source that is down must not bring down the load of the others."""

    def test_the_failure_of_one_source_does_not_block_the_others(
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

    def test_the_source_failure_shows_up_in_the_result(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "properties.services.active_importers", lambda: [BrokenImporter()],
        )

        result = import_all_properties()

        assert any("unavailable" in error for error in result.errors)

    def test_a_missing_file_is_treated_as_a_source_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "properties.services.active_importers",
            lambda: [CSVPropertyImporter(tmp_path / "does-not-exist.csv")],
        )

        result = import_all_properties()

        assert result.total_processed == 0
        assert len(result.errors) == 1

    def test_record_errors_show_up_in_the_consolidated_result(
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


class TestLoadTask:
    def test_returns_the_serializable_summary_of_the_load(self) -> None:
        summary = load_properties()

        assert summary == {
            "created": TOTAL_IN_THE_REAL_SOURCES, "updated": 0, "skipped": 0,
            "errors": 0,
        }
        assert json.dumps(summary)  # has to cross Celery's result backend

    def test_runs_the_same_load_as_the_orchestration(self) -> None:
        load_properties()

        assert Property.objects.count() == TOTAL_IN_THE_REAL_SOURCES

    def test_the_daily_run_is_idempotent(self) -> None:
        load_properties()

        summary = load_properties()

        assert summary["created"] == 0
        assert summary["updated"] == TOTAL_IN_THE_REAL_SOURCES
        assert Property.objects.count() == TOTAL_IN_THE_REAL_SOURCES

    def test_counts_the_errors_instead_of_propagating_them(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The task must not blow up: Beat would reschedule the whole load."""

        monkeypatch.setattr(
            "properties.services.active_importers", lambda: [BrokenImporter()],
        )

        summary = load_properties()

        assert summary == {"created": 0, "updated": 0, "skipped": 0, "errors": 1}
