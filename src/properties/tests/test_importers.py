"""Importadores CSV e JSON.

O contrato de um importador tem três partes: mapear o registro da fonte para o
domínio, ser idempotente (recarregar não duplica nem congela dado velho) e
tolerar registro ruim sem derrubar a carga inteira.
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


class TestCSVMapeamento:
    def test_mapeia_todos_os_campos_do_registro(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        CSVPropertyImporter(write_csv(csv_row())).load()

        imovel = Property.objects.get()
        assert imovel.code == "IMV-001"
        assert imovel.transaction_type == TransactionType.RENT
        assert imovel.neighborhood == "Boa Viagem"
        assert imovel.price == Decimal("2500")
        assert imovel.bedrooms == 2
        assert imovel.address == "Rua dos Navegantes, 150"

    def test_extrai_o_codigo_e_o_remove_da_descricao(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        """O código vira coluna própria; a descrição que o cliente lê não repete."""

        CSVPropertyImporter(
            write_csv(csv_row(descricao="Ótimo imóvel. codigo:IMV-042"))
        ).load()

        imovel = Property.objects.get()
        assert imovel.code == "IMV-042"
        assert imovel.description == "Ótimo imóvel"

    def test_registra_a_origem_da_carga(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        file_path = write_csv(csv_row())

        CSVPropertyImporter(file_path).load()

        imovel = Property.objects.get()
        assert imovel.source == PropertySource.CSV
        assert imovel.imported_at is not None

    def test_arquivo_com_apenas_o_cabecalho_nao_grava_nada(
        self, write_csv: WriteCsv,
    ) -> None:
        result = CSVPropertyImporter(write_csv()).load()

        assert result.total_processed == 0
        assert Property.objects.count() == 0


class TestJSONMapeamento:
    def test_mapeia_o_registro_e_extrai_o_codigo_pelo_prefixo_ref(
        self, write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        """O JSON usa ``ref:`` onde o CSV usa ``codigo:`` — só o padrão muda."""

        JSONPropertyImporter(write_json([json_record()])).load()

        imovel = Property.objects.get()
        assert imovel.code == "C011"
        assert imovel.description == "Apartamento com 2 quartos"
        assert imovel.neighborhood == "Espinheiro"
        assert imovel.source == PropertySource.JSON

    def test_aceita_preco_numerico(
        self, write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        JSONPropertyImporter(write_json([json_record(preco=850000)])).load()

        assert Property.objects.get().price == Decimal("850000")

    def test_lista_vazia_nao_grava_nada(self, write_json: WriteJson) -> None:
        result = JSONPropertyImporter(write_json([])).load()

        assert result.total_processed == 0
        assert Property.objects.count() == 0

    def test_payload_que_nao_e_lista_falha_com_mensagem_clara(
        self, write_json: WriteJson,
    ) -> None:
        importer = JSONPropertyImporter(write_json({"imoveis": []}))

        with pytest.raises(ValueError, match="esperado uma lista"):
            importer.load()

    def test_item_que_nao_e_objeto_falha_com_mensagem_clara(
        self, write_json: WriteJson,
    ) -> None:
        importer = JSONPropertyImporter(write_json(["IMV-001"]))

        with pytest.raises(ValueError, match="esperado objetos na lista"):
            importer.load()


class TestIdempotencia:
    """Recarregar a mesma fonte atualiza; nunca duplica."""

    def test_carga_repetida_nao_duplica_registros(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(
            write_csv(csv_row(), csv_row(descricao="Outro. codigo:IMV-002"))
        )

        importer.load()
        importer.load()
        importer.load()

        assert Property.objects.count() == 2

    def test_conta_criados_na_primeira_carga_e_atualizados_na_segunda(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(write_csv(csv_row()))

        primeira = importer.load()
        segunda = importer.load()

        assert (primeira.created, primeira.updated) == (1, 0)
        assert (segunda.created, segunda.updated) == (0, 1)

    def test_recarga_atualiza_o_registro_existente(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        """Preço muda; um registro velho faria o assistente informar valor errado."""

        CSVPropertyImporter(write_csv(csv_row(preco="2500"))).load()

        CSVPropertyImporter(write_csv(csv_row(preco="2900"))).load()

        assert Property.objects.get().price == Decimal("2900")
        assert Property.objects.count() == 1

    def test_a_recarga_atualiza_o_carimbo_de_importacao(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        importer = CSVPropertyImporter(write_csv(csv_row()))
        importer.load()
        primeiro_carimbo = Property.objects.get().imported_at
        assert primeiro_carimbo is not None

        importer.load()

        segundo_carimbo = Property.objects.get().imported_at
        assert segundo_carimbo is not None
        assert segundo_carimbo > primeiro_carimbo

    def test_mesmo_codigo_em_fontes_diferentes_nao_duplica(
        self, write_csv: WriteCsv, csv_row: CsvRow,
        write_json: WriteJson, json_record: JsonRecord,
    ) -> None:
        csv_path = write_csv(csv_row(descricao="Do CSV. codigo:DUP-1"))
        json_path = write_json(
            [json_record(descricao="Do JSON. ref: DUP-1", preco=9999)]
        )

        CSVPropertyImporter(csv_path).load()
        JSONPropertyImporter(json_path).load()

        imovel = Property.objects.get()
        assert Property.objects.count() == 1
        # A última carga vence, e o rastro de origem diz de onde veio o valor atual.
        assert imovel.price == Decimal("9999")
        assert imovel.source == PropertySource.JSON


class TestRegistroInvalido:
    """Um registro ruim é ignorado e contabilizado; a carga continua."""

    def test_registro_sem_codigo_e_ignorado_sem_derrubar_a_carga(
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

    def test_tipo_de_negocio_desconhecido_e_ignorado(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(tipo="permuta"))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0
        assert "tipo_negocio" in result.errors[0]

    @pytest.mark.parametrize("preco", ["", "sob consulta", "R$ 2.500"])
    def test_preco_invalido_e_ignorado(
        self, write_csv: WriteCsv, csv_row: CsvRow, preco: str,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(preco=preco))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_quartos_invalido_e_ignorado(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(write_csv(csv_row(quartos="dois"))).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_coluna_ausente_e_tratada_como_registro_ignorado(
        self, write_csv: WriteCsv,
    ) -> None:
        """Cabeçalho sem ``bairro``: ``KeyError`` vira registro ignorado, não crash."""

        file_path = write_csv(
            'aluguel,2500,2,"Rua X, 1","Apto. codigo:IMV-001"\n',
            header="tipo_negocio,preco,quartos,endereco,descricao\n",
        )

        result = CSVPropertyImporter(file_path).load()

        assert result.skipped == 1
        assert Property.objects.count() == 0

    def test_registro_ruim_nao_impede_os_seguintes(
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

    def test_o_erro_registrado_identifica_o_registro_problematico(
        self, write_csv: WriteCsv, csv_row: CsvRow,
    ) -> None:
        result = CSVPropertyImporter(
            write_csv(csv_row(descricao="Imóvel sem código"))
        ).load()

        assert "código não encontrado" in result.errors[0]
        assert "Imóvel sem código" in result.errors[0]


class TestArquivosReaisDoDesafio:
    def test_as_duas_fontes_carregam_e_se_mesclam_numa_tabela(
        self, settings: Any,
    ) -> None:
        """Fumaça sobre os dados versionados: 10 do CSV + 10 do JSON, sem colisão."""

        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

        codigos = list(Property.objects.values_list("code", flat=True))
        assert len(codigos) == len(set(codigos)) == 20
        assert Property.objects.filter(source=PropertySource.CSV).count() == 10
        assert Property.objects.filter(source=PropertySource.JSON).count() == 10

    def test_nenhuma_descricao_carrega_a_marcacao_de_codigo(
        self, settings: Any,
    ) -> None:
        CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()
        JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

        assert not Property.objects.filter(description__icontains="codigo:").exists()
        assert not Property.objects.filter(description__icontains="ref:").exists()
