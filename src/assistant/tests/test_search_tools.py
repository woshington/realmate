"""Tool de busca de imóveis.

As regras testadas aqui são as que impedem o assistente de inventar imóvel:
sem filtro obrigatório não há busca, um imóvel já mostrado não volta, e cada
resposta traz no máximo dois imóveis.
"""

from decimal import Decimal
from typing import Any, Callable

import pytest

from assistant.tools import (
    FOUND,
    NOTHING_FOUND,
    SEARCH_BUDGET_SPENT,
    AssistantDeps,
)
from assistant.tools.search_properties_tools import (
    MAX_RESULTS,
    MAX_SEARCHES_PER_RUN,
    search_properties,
)

from .conftest import PropertyORM

CallTool = Callable[..., Any]
MakeProperty = Callable[..., Any]


def search(
    call_tool: CallTool,
    deps: AssistantDeps | None = None,
    /,
    **filters: Any,
) -> Any:
    return call_tool(search_properties, deps, **filters)


class TestFiltrosObrigatorios:
    """Sem os dados obrigatórios a tool recusa e manda perguntar ao cliente."""

    @pytest.mark.parametrize(
        ("filtros", "dado_faltante"),
        [
            ({}, "tipo de transação"),
            ({}, "bairro"),
            ({}, "preço"),
            ({"neighborhood": "Boa Viagem", "max_price": 3000}, "tipo de transação"),
            ({"transaction_type": "aluguel", "max_price": 3000}, "bairro"),
            ({"transaction_type": "aluguel", "neighborhood": "Boa Viagem"}, "preço"),
        ],
    )
    def test_orienta_a_perguntar_o_dado_que_falta(
        self, call_tool: CallTool, orm: PropertyORM,
        filtros: dict[str, Any], dado_faltante: str,
    ) -> None:
        result = search(call_tool, **filtros)

        assert dado_faltante in result.guidance
        assert "Pergunte ao cliente" in result.guidance

    def test_nao_consulta_o_banco_quando_falta_filtro(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(call_tool, transaction_type="aluguel", neighborhood="Boa Viagem")

        assert orm.searched is False

    def test_nao_devolve_imovel_quando_falta_filtro(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub())

        result = search(call_tool, transaction_type="aluguel")

        assert result.properties == []

    def test_busca_recusada_nao_gasta_orcamento_de_buscas(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        search(call_tool, deps, transaction_type="aluguel")

        assert deps.searches_done == 0

    @pytest.mark.parametrize("preco", [{"min_price": 1000}, {"max_price": 3000}])
    def test_um_extremo_de_preco_ja_satisfaz_o_filtro_obrigatorio(
        self, call_tool: CallTool, orm: PropertyORM, preco: dict[str, int],
    ) -> None:
        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            **preco,
        )

        assert orm.searched is True

    def test_codigo_dispensa_todos_os_outros_filtros(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(call_tool, code="IMV-001")

        assert orm.filters == {"code": "IMV-001"}


class TestNaoRepeteImovel:
    """Imóvel já apresentado na conversa não pode voltar."""

    def test_exclui_os_imoveis_ja_recomendados_na_conversa(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        orm.already_recommended(5, 9)

        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            max_price=3000,
        )

        assert orm.excluded_ids == [5, 9]

    def test_consulta_as_recomendacoes_da_conversa_certa(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=42)

        search(call_tool, deps, code="IMV-001")

        orm.recommendation.objects.filter.assert_called_once_with(conversation_id=42)

    def test_exclui_tambem_na_busca_por_codigo(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        orm.already_recommended(7)

        search(call_tool, code="IMV-001")

        assert orm.excluded_ids == [7]

    def test_registra_os_codigos_apresentados_para_a_task(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        orm.returns(property_stub(code="IMV-007"))

        search(call_tool, deps, code="IMV-007")

        assert deps.presented_codes == ["IMV-007"]

    def test_acumula_os_codigos_de_buscas_sucessivas(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        orm.returns(property_stub(code="IMV-001"))
        search(call_tool, deps, code="IMV-001")
        orm.returns(property_stub(code="IMV-002"))
        search(call_tool, deps, code="IMV-002")

        assert deps.presented_codes == ["IMV-001", "IMV-002"]


class TestLimiteDeResultados:
    """No máximo dois imóveis por resposta."""

    def test_devolve_no_maximo_dois_imoveis(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(5)])

        result = search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            max_price=3000,
        )

        assert len(result.properties) == MAX_RESULTS == 2

    def test_apresenta_os_dois_primeiros_da_query(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(5)])

        result = search(call_tool, code="IMV-0")

        assert [imovel.code for imovel in result.properties] == ["IMV-0", "IMV-1"]

    def test_o_limite_vale_tambem_para_os_codigos_apresentados(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(5)])

        search(call_tool, deps, code="IMV-0")

        assert len(deps.presented_codes) == MAX_RESULTS


class TestOrcamentoDeBuscas:
    """Uma mensagem do cliente não pode virar uma varredura no catálogo."""

    def test_recusa_a_busca_quando_o_orcamento_acabou(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        result = search(call_tool, deps, code="IMV-001")

        assert result.guidance == SEARCH_BUDGET_SPENT
        assert result.properties == []
        assert orm.searched is False

    def test_nao_incrementa_o_contador_depois_do_limite(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        search(call_tool, deps, code="IMV-001")

        assert deps.searches_done == MAX_SEARCHES_PER_RUN

    def test_cada_busca_efetiva_consome_uma_unidade(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        search(call_tool, deps, code="IMV-001")
        search(call_tool, deps, code="IMV-002")

        assert deps.searches_done == 2


class TestFiltrosDaQuery:
    """O que o cliente informou vira filtro; o que ele não informou, não."""

    def test_monta_a_query_com_todos_os_filtros_informados(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(
            call_tool,
            transaction_type="venda",
            neighborhood="Boa Viagem",
            min_price=100000,
            max_price=300000,
            bedrooms=3,
        )

        assert orm.filters == {
            "transaction_type": "venda",
            "neighborhood__iexact": "Boa Viagem",
            "price__gte": Decimal("100000"),
            "price__lte": Decimal("300000"),
            "bedrooms": 3,
        }

    def test_quartos_e_opcional_e_fica_fora_da_query_quando_ausente(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            max_price=3000,
        )

        assert "bedrooms" not in orm.filters

    def test_a_faixa_informada_pelo_cliente_vira_filtro_da_query(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=1500,
            max_price=3000,
        )

        assert orm.filters["price__gte"] == 1500
        assert orm.filters["price__lte"] == 3000


class TestOrientacaoDaResposta:
    """O campo ``guidance`` é o que diz ao modelo o que fazer em seguida."""

    def test_orienta_a_apresentar_quando_encontra(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub())

        result = search(call_tool, code="IMV-001")

        assert result.guidance == FOUND

    def test_orienta_a_ser_transparente_quando_nao_encontra(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        orm.returns()

        result = search(call_tool, code="IMV-404")

        assert result.guidance == NOTHING_FOUND
        assert result.properties == []

    def test_converte_o_imovel_para_o_formato_da_resposta(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(
            property_stub(
                code="IMV-001",
                price="2500.00",
                neighborhood="Boa Viagem",
                bedrooms=2,
                address="Rua dos Navegantes, 150",
                description="Apartamento com varanda",
            )
        )

        imovel = search(call_tool, code="IMV-001").properties[0]

        assert (imovel.code, imovel.price, imovel.bedrooms) == ("IMV-001", 2500, 2)
        assert imovel.neighborhood == "Boa Viagem"
        assert imovel.address == "Rua dos Navegantes, 150"
        assert imovel.description == "Apartamento com varanda"
