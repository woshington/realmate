import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from properties.enums import PropertySource, TransactionType
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.models import Property

pytestmark = pytest.mark.django_db

CSV_HEADER = "tipo_negocio,preco,quartos,bairro,endereco,descricao\n"


def write_csv(path: Path, *rows: str) -> Path:
    file_path = path / "imoveis.csv"
    file_path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return file_path


def csv_row(
    *,
    tipo: str = "aluguel",
    preco: str = "2500",
    quartos: str = "2",
    bairro: str = "Boa Viagem",
    endereco: str = "Rua dos Navegantes, 150",
    descricao: str = "Apartamento com varanda. codigo:IMV-001",
) -> str:
    return f'{tipo},{preco},{quartos},{bairro},"{endereco}","{descricao}"\n'


def write_json(path: Path, payload: Any) -> Path:
    file_path = path / "imoveis.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return file_path


def json_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "tipo_negocio": "aluguel",
        "preco": 2200,
        "quartos": 2,
        "bairro": "Espinheiro",
        "endereco": "Rua do Espinheiro, 340",
        "descricao": "Apartamento com 2 quartos. ref: C011",
    }
    record.update(overrides)
    return record


# --- CSV --------------------------------------------------------------------


def test_csv_mapeia_todos_os_campos_do_registro(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(write_csv(tmp_path, csv_row()))

    importer.load()

    imovel = Property.objects.get()
    assert imovel.code == "IMV-001"
    assert imovel.transaction_type == TransactionType.RENT
    assert imovel.neighborhood == "Boa Viagem"
    assert imovel.price == Decimal("2500")
    assert imovel.bedrooms == 2
    assert imovel.address == "Rua dos Navegantes, 150"


def test_csv_extrai_o_codigo_e_o_remove_da_descricao(tmp_path: Path) -> None:
    """O código vive em coluna própria; a descrição que o cliente lê não repete."""

    importer = CSVPropertyImporter(
        write_csv(tmp_path, csv_row(descricao="Ótimo imóvel. codigo:IMV-042"))
    )

    importer.load()

    imovel = Property.objects.get()
    assert imovel.code == "IMV-042"
    assert imovel.description == "Ótimo imóvel"
    assert "codigo" not in imovel.description


def test_csv_registra_a_origem_da_carga(tmp_path: Path) -> None:
    file_path = write_csv(tmp_path, csv_row())

    CSVPropertyImporter(file_path).load()

    imovel = Property.objects.get()
    assert imovel.source == PropertySource.CSV
    assert imovel.source_reference == str(file_path)
    assert imovel.imported_at is not None


def test_csv_conta_criados_e_atualizados(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(write_csv(tmp_path, csv_row()))

    primeira = importer.load()
    segunda = importer.load()

    assert (primeira.created, primeira.updated) == (1, 0)
    assert (segunda.created, segunda.updated) == (0, 1)


# --- Idempotência -----------------------------------------------------------


def test_carga_repetida_nao_duplica_registros(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(
        write_csv(tmp_path, csv_row(), csv_row(descricao="Outro. codigo:IMV-002"))
    )

    importer.load()
    importer.load()
    importer.load()

    assert Property.objects.count() == 2


def test_recarga_atualiza_o_registro_existente(tmp_path: Path) -> None:
    """Preço muda; um registro velho faria o assistente informar valor errado."""

    CSVPropertyImporter(write_csv(tmp_path, csv_row(preco="2500"))).load()

    CSVPropertyImporter(write_csv(tmp_path, csv_row(preco="2900"))).load()

    imovel = Property.objects.get()
    assert imovel.price == Decimal("2900")
    assert Property.objects.count() == 1


def test_mesmo_codigo_em_fontes_diferentes_nao_duplica(tmp_path: Path) -> None:
    csv_path = write_csv(tmp_path, csv_row(descricao="Do CSV. codigo:DUP-1"))
    json_path = write_json(
        tmp_path, [json_record(descricao="Do JSON. ref: DUP-1", preco=9999)]
    )

    CSVPropertyImporter(csv_path).load()
    JSONPropertyImporter(json_path).load()

    imovel = Property.objects.get()
    assert Property.objects.count() == 1
    # A última carga vence, e o rastro de origem diz de onde veio o valor atual.
    assert imovel.price == Decimal("9999")
    assert imovel.source == PropertySource.JSON


# --- Registros inválidos ----------------------------------------------------


def test_registro_sem_codigo_e_ignorado_sem_derrubar_a_carga(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(
        write_csv(
            tmp_path,
            csv_row(descricao="Imóvel sem código nenhum"),
            csv_row(descricao="Esse tem. codigo:IMV-009"),
        )
    )

    result = importer.load()

    assert result.skipped == 1
    assert result.created == 1
    assert len(result.errors) == 1
    assert Property.objects.get().code == "IMV-009"


def test_tipo_de_negocio_desconhecido_e_ignorado(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(write_csv(tmp_path, csv_row(tipo="permuta")))

    result = importer.load()

    assert result.skipped == 1
    assert Property.objects.count() == 0
    assert "tipo_negocio" in result.errors[0]


@pytest.mark.parametrize("preco", ["", "sob consulta", "R$ 2.500"])
def test_preco_invalido_e_ignorado(tmp_path: Path, preco: str) -> None:
    result = CSVPropertyImporter(write_csv(tmp_path, csv_row(preco=preco))).load()

    assert result.skipped == 1
    assert Property.objects.count() == 0


def test_quartos_invalido_e_ignorado(tmp_path: Path) -> None:
    result = CSVPropertyImporter(write_csv(tmp_path, csv_row(quartos="dois"))).load()

    assert result.skipped == 1
    assert Property.objects.count() == 0


def test_coluna_ausente_e_ignorada_como_erro_de_registro(tmp_path: Path) -> None:
    """Cabeçalho sem `bairro`: KeyError vira registro ignorado, não crash."""

    file_path = tmp_path / "imoveis.csv"
    file_path.write_text(
        "tipo_negocio,preco,quartos,endereco,descricao\n"
        'aluguel,2500,2,"Rua X, 1","Apto. codigo:IMV-001"\n',
        encoding="utf-8",
    )

    result = CSVPropertyImporter(file_path).load()

    assert result.skipped == 1
    assert Property.objects.count() == 0


def test_erros_de_um_registro_nao_impedem_os_seguintes(tmp_path: Path) -> None:
    importer = CSVPropertyImporter(
        write_csv(
            tmp_path,
            csv_row(tipo="permuta", descricao="A. codigo:A-1"),
            csv_row(descricao="B. codigo:B-2"),
            csv_row(preco="x", descricao="C. codigo:C-3"),
            csv_row(descricao="D. codigo:D-4"),
        )
    )

    result = importer.load()

    assert result.created == 2
    assert result.skipped == 2
    assert set(Property.objects.values_list("code", flat=True)) == {"B-2", "D-4"}


# --- JSON -------------------------------------------------------------------


def test_json_mapeia_o_registro_e_extrai_o_codigo_pelo_prefixo_ref(
    tmp_path: Path,
) -> None:
    """O JSON usa `ref:` onde o CSV usa `codigo:` — só o padrão muda."""

    importer = JSONPropertyImporter(write_json(tmp_path, [json_record()]))

    importer.load()

    imovel = Property.objects.get()
    assert imovel.code == "C011"
    assert imovel.description == "Apartamento com 2 quartos"
    assert imovel.neighborhood == "Espinheiro"
    assert imovel.source == PropertySource.JSON


def test_json_aceita_preco_numerico(tmp_path: Path) -> None:
    importer = JSONPropertyImporter(write_json(tmp_path, [json_record(preco=850000)]))

    importer.load()

    assert Property.objects.get().price == Decimal("850000")


def test_json_que_nao_e_lista_falha_com_mensagem_clara(tmp_path: Path) -> None:
    importer = JSONPropertyImporter(write_json(tmp_path, {"imoveis": []}))

    with pytest.raises(ValueError, match="esperado uma lista"):
        importer.load()


def test_json_com_item_que_nao_e_objeto_falha(tmp_path: Path) -> None:
    importer = JSONPropertyImporter(write_json(tmp_path, ["IMV-001"]))

    with pytest.raises(ValueError, match="esperado objetos na lista"):
        importer.load()


def test_json_vazio_nao_grava_nada(tmp_path: Path) -> None:
    result = JSONPropertyImporter(write_json(tmp_path, [])).load()

    assert result.total_processed == 0
    assert Property.objects.count() == 0


# --- Arquivos reais do desafio ----------------------------------------------


def test_arquivos_do_desafio_carregam_e_se_mesclam_numa_tabela(
    settings: Any,
) -> None:
    """Fumaça sobre os dados reais: 10 do CSV + 10 do JSON, códigos únicos."""

    CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()
    JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

    codigos = list(Property.objects.values_list("code", flat=True))
    assert len(codigos) == 20
    assert len(set(codigos)) == 20
    assert Property.objects.filter(source=PropertySource.CSV).count() == 10
    assert Property.objects.filter(source=PropertySource.JSON).count() == 10
    assert not Property.objects.filter(description__icontains="codigo:").exists()
    assert not Property.objects.filter(description__icontains="ref:").exists()
