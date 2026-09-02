"""CSV and JSON importers.

An importer contract has three parts: map the source record onto the domain, be
idempotent (reloading neither duplicates nor freezes stale data) and tolerate a
bad record without bringing the whole load down.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pytest

from properties.enums import PropertySource, TransactionType
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.models import Property

pytestmark = pytest.mark.django_db

WriteCsv = Callable[..., Path]
CsvRow = Callable[..., str]
WriteJson = Callable[..., Path]
JsonRecord = Callable[..., dict[str, Any]]


class TestCSVMapping:
    def test_maps_every_field_of_the_record(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        CSVPropertyImporter(write_csv(csv_row())).load()

        property_ = Property.objects.get()
        assert property_.code == "IMV-001"
        assert property_.transaction_type == TransactionType.RENT
        assert property_.neighborhood == "Boa Viagem"
        assert property_.price == Decimal("2500")
        assert property_.bedrooms == 2
        assert property_.address == "Rua dos Navegantes, 150"

    def test_extracts_the_code_and_strips_it_from_the_description(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        """The code becomes its own column; the description the customer reads does not repeat it."""

        CSVPropertyImporter(
            write_csv(csv_row(descricao="Ótimo imóvel. codigo:IMV-042"))
        ).load()

        property_ = Property.objects.get()
        assert property_.code == "IMV-042"
        assert property_.description == "Ótimo imóvel"

    def test_records_the_origin_of_the_load(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        file_path = write_csv(csv_row())

        CSVPropertyImporter(file_path).load()

        property_ = Property.objects.get()
        assert property_.source == PropertySource.CSV
        assert property_.imported_at is not None

    def test_a_file_with_only_the_header_stores_nothing(
        self, write_csv: WriteCsv,
    ) -> None:
        result = CSVPropertyImporter(write_csv()).load()

        assert result.total_processed == 0
        assert Property.objects.count() == 0


class TestJSONMapping:
    def test_maps_the_record_and_extracts_the_code_from_the_ref_prefix(
        self, write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        """The JSON uses ``ref:`` where the CSV uses ``codigo:`` — only the pattern changes."""

        JSONPropertyImporter(write_json([json_record()])).load()

        property_ = Property.objects.get()
        assert property_.code == "C011"
        assert property_.description == "Apartamento com 2 quartos"
        assert property_.neighborhood == "Espinheiro"
        assert property_.source == PropertySource.JSON

    def test_accepts_a_numeric_price(
        self, write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        JSONPropertyImporter(write_json([json_record(preco=850000)])).load()

        assert Property.objects.get().price == Decimal("850000")

    def test_an_empty_list_stores_nothing(self, write_json: WriteJson) -> None:
        result = JSONPropertyImporter(write_json([])).load()

        assert result.total_processed == 0
        assert Property.objects.count() == 0

    def test_a_payload_that_is_not_a_list_fails_with_a_clear_message(
        self, write_json: WriteJson,
    ) -> None:
        importer = JSONPropertyImporter(write_json({"imoveis": []}))

        with pytest.raises(ValueError, match="esperado uma lista"):
            importer.load()

    def test_an_item_that_is_not_an_object_fails_with_a_clear_message(
        self, write_json: WriteJson,
    ) -> None:
        importer = JSONPropertyImporter(write_json(["IMV-001"]))

        with pytest.raises(ValueError, match="esperado objetos na lista"):
            importer.load()


class TestIdempotence:
    """Reloading the same source updates; it never duplicates."""

    def test_a_repeated_load_does_not_duplicate_records(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(
            write_csv(csv_row(), csv_row(descricao="Outro. codigo:IMV-002"))
        )

        importer.load()
        importer.load()
        importer.load()

        assert Property.objects.count() == 2

    def test_counts_created_on_the_first_load_and_updated_on_the_second(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(write_csv(csv_row()))

        first = importer.load()
        second = importer.load()

        assert (first.created, first.updated) == (1, 0)
        assert (second.created, second.updated) == (0, 1)

    def test_a_reload_updates_the_existing_record(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        """Prices change; a stale record would make the assistant quote the wrong value."""

        CSVPropertyImporter(write_csv(csv_row(preco="2500"))).load()

        CSVPropertyImporter(write_csv(csv_row(preco="2900"))).load()

        assert Property.objects.get().price == Decimal("2900")
        assert Property.objects.count() == 1

    def test_a_reload_updates_the_import_timestamp(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(write_csv(csv_row()))
        importer.load()
        first_timestamp = Property.objects.get().imported_at
        assert first_timestamp is not None

        importer.load()

        second_timestamp = Property.objects.get().imported_at
        assert second_timestamp is not None
        assert second_timestamp > first_timestamp

    def test_the_same_code_from_different_sources_does_not_duplicate(
        self, write_csv: WriteCsv, csv_row: CsvRow,
        write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        csv_path = write_csv(csv_row(descricao="Do CSV. codigo:DUP-1"))
        json_path = write_json(
            [json_record(descricao="Do JSON. ref: DUP-1", preco=9999)]
        )

        CSVPropertyImporter(csv_path).load()
        JSONPropertyImporter(json_path).load()

        property_ = Property.objects.get()
        assert Property.objects.count() == 1
        # The last load wins, and the origin trail says where the current value came from.
        assert property_.price == Decimal("9999")
        assert property_.source == PropertySource.JSON


class TestInvalidRecord:
    """A bad record is skipped and counted; the load carries on."""

    def test_a_record_without_a_code_is_skipped_without_breaking_the_load(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(
            write_csv(
                csv_row(descricao="Imóvel sem código nenhum"),
                csv_row(descricao="Esse tem. codigo:IMV-009"),
            )
        ).load()

        assert (result.created, result.skipped) == (1, 1)
        assert len(result.errors) == 1
        assert Property.objects.get().code == "IMV-009"

    def test_an_unknown_transaction_type_is_skipped(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(tipo="permuta"))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0
        assert "tipo_negocio" in result.errors[0]

    @pytest.mark.parametrize("price", ["", "sob consulta", "R$ 2.500"])
    def test_an_invalid_price_is_skipped(
        self, write_csv: WriteCsv, csv_row: CsvRow, price: str,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(preco=price))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_an_invalid_bedroom_count_is_skipped(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(quartos="dois"))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_a_missing_column_is_treated_as_a_skipped_record(
        self, write_csv: WriteCsv,
    ) -> None:
        """Header without ``bairro``: the ``KeyError`` becomes a skipped record, not a crash."""

        file_path = write_csv(
            'aluguel,2500,2,"Rua X, 1","Apto. codigo:IMV-001"\n',
            header="tipo_negocio,preco,quartos,endereco,descricao\n",
        )

        result = CSVPropertyImporter(file_path).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_a_bad_record_does_not_block_the_following_ones(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(
            write_csv(
                csv_row(tipo="permuta", descricao="A. codigo:A-1"),
                csv_row(descricao="B. codigo:B-2"),
                csv_row(preco="x", descricao="C. codigo:C-3"),
                csv_row(descricao="D. codigo:D-4"),
            )
        ).load()

        assert (result.created, result.skipped) == (2, 2)
        assert set(Property.objects.values_list("code", flat=True)) == {"B-2", "D-4"}

    def test_the_recorded_error_identifies_the_problematic_record(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(
            write_csv(csv_row(descricao="Imóvel sem código"))
        ).load()

        assert "código não encontrado" in result.errors[0]
        assert "Imóvel sem código" in result.errors[0]


class TestRealChallengeFiles:
    def test_both_sources_load_and_merge_into_one_table(
        self, settings: Any,
    ) -> None:
        """Smoke test over the versioned data: 10 from the CSV + 10 from the JSON, no collision."""

        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

        codes = list(Property.objects.values_list("code", flat=True))
        assert len(codes) == len(set(codes)) == 20
        assert Property.objects.filter(source=PropertySource.CSV).count() == 10
        assert Property.objects.filter(source=PropertySource.JSON).count() == 10

    def test_no_description_carries_the_code_marker(
        self, settings: Any,
    ) -> None:
        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

        assert not Property.objects.filter(description__icontains="codigo:").exists()
        assert not Property.objects.filter(description__icontains="ref:").exists()
