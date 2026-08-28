import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from properties.enums import PropertySource, TransactionType
from properties.importers.etl.csv import CSVPropertyImporter
from properties.importers.etl.json import JSONPropertyImporter
from properties.models import Property
from properties.tasks import load_properties

pytestmark = pytest.mark.django_db

REGISTRO = {
    "tipo_negocio": "aluguel",
    "preco": 2200,
    "quartos": 2,
    "bairro": "Espinheiro",
    "endereco": "Rua do Espinheiro, 340",
    "descricao": "Apartamento com 2 quartos e área de serviço. ref: C011",
}


def write_json(tmp_path: Path, payload: Any) -> Path:
    file_path = tmp_path / "imoveis.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    return file_path


# --- arquivo real -----------------------------------------------------------


def test_importa_os_dez_imoveis_do_arquivo_do_desafio() -> None:
    result = JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

    assert (result.created, result.updated, result.skipped) == (10, 0, 0)
    assert result.errors == []
    assert Property.objects.count() == 10


def test_codigos_do_arquivo_real_saem_do_marcador_ref() -> None:
    JSONPropertyImporter(settings.PROPERTIES_JSON_PATH).load()

    assert set(Property.objects.values_list("code", flat=True)) == {
        f"C0{numero}" for numero in range(11, 21)
    }


# --- transformação ----------------------------------------------------------


def test_codigo_sai_da_descricao_e_nao_fica_no_texto(tmp_path: Path) -> None:
    JSONPropertyImporter(write_json(tmp_path, [REGISTRO])).load()

    imovel = Property.objects.get()
    assert imovel.code == "C011"
    assert "ref:" not in imovel.description
    assert imovel.description == "Apartamento com 2 quartos e área de serviço"


def test_numeros_do_json_viram_decimal_e_int(tmp_path: Path) -> None:
    JSONPropertyImporter(write_json(tmp_path, [REGISTRO])).load()

    imovel = Property.objects.get()
    assert imovel.price == Decimal("2200")
    assert imovel.bedrooms == 2


def test_preco_com_centavos_nao_passa_por_float(tmp_path: Path) -> None:
    """`Decimal(str(...))` em vez de `Decimal(float)`: 2200.10 não vira dízima."""
    JSONPropertyImporter(write_json(tmp_path, [{**REGISTRO, "preco": 2200.10}])).load()

    assert Property.objects.get().price == Decimal("2200.10")


@pytest.mark.parametrize(
    ("rotulo", "esperado"),
    [
        ("aluguel", TransactionType.RENT),
        ("venda", TransactionType.SALE),
        ("Aluguel", TransactionType.RENT),
        (" VENDA ", TransactionType.SALE),
    ],
)
def test_tipo_de_negocio_e_normalizado(
    tmp_path: Path, rotulo: str, esperado: str
) -> None:
    registro = {**REGISTRO, "tipo_negocio": rotulo}

    JSONPropertyImporter(write_json(tmp_path, [registro])).load()

    assert Property.objects.get().transaction_type == esperado


def test_origem_e_referencia_ficam_registradas(tmp_path: Path) -> None:
    file_path = write_json(tmp_path, [REGISTRO])

    JSONPropertyImporter(file_path).load()

    imovel = Property.objects.get()
    assert imovel.source == PropertySource.JSON
    assert imovel.source_reference == str(file_path)
    assert imovel.imported_at is not None


# --- registros ruins --------------------------------------------------------


@pytest.mark.parametrize(
    ("registro", "motivo"),
    [
        pytest.param({"descricao": "sem marcador de código"}, "código", id="sem-codigo"),
        pytest.param({"tipo_negocio": "permuta"}, "tipo_negocio", id="negocio-invalido"),
        pytest.param({"preco": "dois mil"}, "preço", id="preco-invalido"),
        pytest.param({"quartos": "muitos"}, "quartos", id="quartos-invalido"),
    ],
)
def test_registro_invalido_e_ignorado_com_erro(
    tmp_path: Path, registro: dict[str, Any], motivo: str
) -> None:
    """Um registro ruim não pode abortar a carga inteira."""
    payload = [{**REGISTRO, **registro}, {**REGISTRO, "descricao": "Outro. ref: C012"}]

    result = JSONPropertyImporter(write_json(tmp_path, payload)).load()

    assert (result.created, result.skipped) == (1, 1)
    assert motivo in result.errors[0]
    assert list(Property.objects.values_list("code", flat=True)) == ["C012"]


def test_campo_ausente_e_ignorado_com_erro(tmp_path: Path) -> None:
    registro = {campo: valor for campo, valor in REGISTRO.items() if campo != "bairro"}

    result = JSONPropertyImporter(write_json(tmp_path, [registro])).load()

    assert result.skipped == 1
    assert "bairro" in result.errors[0]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"imoveis": []}, id="objeto-na-raiz"),
        pytest.param(["texto solto"], id="item-que-nao-e-objeto"),
    ],
)
def test_arquivo_com_formato_errado_falha_alto(tmp_path: Path, payload: Any) -> None:
    """Formato errado é falha de carga, não dez registros ignorados em silêncio."""
    importer = JSONPropertyImporter(write_json(tmp_path, payload))

    with pytest.raises(ValueError, match="esperado"):
        importer.load()


# --- recarga ----------------------------------------------------------------


def test_recarga_atualiza_em_vez_de_duplicar(tmp_path: Path) -> None:
    file_path = write_json(tmp_path, [REGISTRO])
    JSONPropertyImporter(file_path).load()

    write_json(tmp_path, [{**REGISTRO, "preco": 2400}])
    result = JSONPropertyImporter(file_path).load()

    assert (result.created, result.updated) == (0, 1)
    assert Property.objects.get().price == Decimal("2400")


# --- task -------------------------------------------------------------------


def test_task_carrega_as_duas_origens() -> None:
    resumo = load_properties()

    assert resumo == {"created": 20, "updated": 0, "skipped": 0, "errors": 0}
    assert Property.objects.filter(source=PropertySource.CSV).count() == 10
    assert Property.objects.filter(source=PropertySource.JSON).count() == 10


def test_csv_continua_lendo_o_seu_proprio_marcador_de_codigo() -> None:
    """O CSV usa `codigo:`; o refactor dos parsers não pode tê-lo quebrado."""
    result = CSVPropertyImporter(settings.PROPERTIES_CSV_PATH).load()

    assert result.created == 10
    assert Property.objects.filter(code="IMV-001").exists()
