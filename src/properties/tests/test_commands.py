"""``manage.py carregar_imoveis``.

É o comando documentado no README para o primeiro setup — o primeiro que alguém
roda ao subir o projeto. Os testes cobrem o que ele imprime, o exit code e o
fato de compartilhar a orquestração com a task agendada.
"""

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from properties.models import Property

pytestmark = pytest.mark.django_db


def run(**kwargs: Any) -> str:
    out = StringIO()
    call_command("carregar_imoveis", stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


def test_command_carrega_as_duas_fontes() -> None:
    run()

    assert Property.objects.count() == 20


def test_command_reporta_o_resumo_da_carga() -> None:
    saida = run()

    assert "criados: 20" in saida
    assert "atualizados: 0" in saida
    assert "Total de imóveis no banco: 20" in saida


def test_rodar_duas_vezes_atualiza_em_vez_de_duplicar() -> None:
    run()

    saida = run()

    assert "criados: 0" in saida
    assert "atualizados: 20" in saida
    assert Property.objects.count() == 20


def test_command_lista_os_registros_ignorados(settings: Any, tmp_path: Path) -> None:
    csv_path = tmp_path / "imoveis.csv"
    csv_path.write_text(
        "tipo_negocio,preco,quartos,bairro,endereco,descricao\n"
        'aluguel,2500,2,Boa Viagem,"Rua X, 1","Sem código aqui"\n'
        'aluguel,1800,1,Graças,"Rua Y, 2","Studio. codigo:OK-1"\n',
        encoding="utf-8",
    )
    settings.PROPERTIES_CSV_PATH = csv_path
    settings.PROPERTIES_JSON_PATH = tmp_path / "vazio.json"
    (tmp_path / "vazio.json").write_text("[]", encoding="utf-8")

    saida = run()

    assert "ignorados: 1" in saida
    assert "código não encontrado" in saida


def test_carga_vazia_falha_em_vez_de_passar_em_silencio(
    settings: Any, tmp_path: Path
) -> None:
    """Zero registro processado é sintoma de arquivo sumido, não de sucesso."""

    vazio_csv = tmp_path / "vazio.csv"
    vazio_csv.write_text(
        "tipo_negocio,preco,quartos,bairro,endereco,descricao\n", encoding="utf-8"
    )
    vazio_json = tmp_path / "vazio.json"
    vazio_json.write_text("[]", encoding="utf-8")
    settings.PROPERTIES_CSV_PATH = vazio_csv
    settings.PROPERTIES_JSON_PATH = vazio_json

    with pytest.raises(CommandError, match="Nenhum registro processado"):
        run()


def test_arquivo_inexistente_nao_derruba_a_outra_fonte(
    settings: Any, tmp_path: Path
) -> None:
    settings.PROPERTIES_CSV_PATH = tmp_path / "nao-existe.csv"

    saida = run()

    # O JSON real continua carregando mesmo com o CSV ausente.
    assert Property.objects.count() == 10
    assert "criados: 10" in saida
