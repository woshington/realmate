from decimal import Decimal
from typing import Any, get_args

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import tool_return_ta

from assistant.schemas import PropertySearchResult
from assistant.tools import (
    MAX_RESULTS,
    MAX_SEARCHES_PER_RUN,
    AssistantDeps,
    TransactionTypeFilter,
    faq_properties,
    search_properties,
)
from conversations.models import Conversation, PropertyRecommendation
from properties.enums import TransactionType
from properties.models import Property

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"


def make_property(
    code: str,
    *,
    transaction_type: str = TransactionType.RENT,
    neighborhood: str = "Boa Viagem",
    price: str = "2500",
    bedrooms: int = 2,
) -> Property:
    return Property.objects.create(
        code=code,
        transaction_type=transaction_type,
        neighborhood=neighborhood,
        price=Decimal(price),
        bedrooms=bedrooms,
        address=f"Rua {code}",
        description=f"Imóvel {code}",
    )


@pytest.fixture
def conversation() -> Conversation:
    return Conversation.objects.create(user_phone=PHONE)


def call(
    conversation: Conversation, *, deps: AssistantDeps | None = None, **filters: Any
) -> PropertySearchResult:
    ctx = _FakeContext(deps or AssistantDeps(conversation_id=conversation.pk))
    return search_properties(ctx, **filters)  # type: ignore[arg-type]


def codes(result: PropertySearchResult) -> list[str]:
    return [property.code for property in result.properties]


class _FakeContext:
    """Stand-in do `RunContext`: a tool só usa `ctx.deps`."""

    def __init__(self, deps: AssistantDeps) -> None:
        self.deps = deps


# --- contrato com o banco ---------------------------------------------------


def test_filtro_de_transacao_usa_os_valores_gravados_no_banco() -> None:
    """Regressão: a tool já expôs 'sale'/'rental', que nunca casam com o banco.

    Quando o `Literal` da tool diverge de `TransactionType`, toda busca por
    bairro/preço volta vazia e o agente entra em loop de novas tentativas.
    """
    aceitos = set(get_args(TransactionTypeFilter))

    assert aceitos == set(TransactionType.values)


def test_resultado_da_busca_e_serializavel_para_o_modelo(
    conversation: Conversation,
) -> None:
    """Regressão: devolver instâncias do Django estoura PydanticSerializationError.

    O pydantic-ai serializa o retorno da tool para montar a próxima requisição,
    e `Property` não é serializável.
    """
    make_property("IMV-001")

    found = call(
        conversation, transaction_type=TransactionType.RENT, neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert tool_return_ta.dump_json(found)


# --- filtros ----------------------------------------------------------------


def test_busca_por_bairro_preco_e_transacao(conversation: Conversation) -> None:
    make_property("IMV-001", price="2500")
    make_property("IMV-002", neighborhood="Casa Forte")
    make_property("IMV-003", transaction_type=TransactionType.SALE, price="850000")

    found = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert codes(found) == ["IMV-001"]


def test_bairro_ignora_diferenca_de_caixa(conversation: Conversation) -> None:
    make_property("IMV-001")

    found = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="boa viagem",
        max_price=Decimal("3000"),
    )

    assert codes(found) == ["IMV-001"]


def test_preco_minimo_maximo_e_faixa(conversation: Conversation) -> None:
    make_property("BARATO", price="1500")
    make_property("MEIO", price="2500")
    make_property("CARO", price="4000")

    base: dict[str, Any] = {
        "transaction_type": TransactionType.RENT,
        "neighborhood": "Boa Viagem",
    }

    piso = call(conversation, **base, min_price=Decimal("2000"))
    teto = call(conversation, **base, max_price=Decimal("2000"))
    faixa = call(
        conversation, **base, min_price=Decimal("2000"), max_price=Decimal("3000")
    )

    assert set(codes(piso)) == {"MEIO", "CARO"}
    assert codes(teto) == ["BARATO"]
    assert codes(faixa) == ["MEIO"]


def test_quartos_e_filtro_opcional(conversation: Conversation) -> None:
    make_property("DOIS", bedrooms=2)
    make_property("TRES", bedrooms=3)

    base: dict[str, Any] = {
        "transaction_type": TransactionType.RENT,
        "neighborhood": "Boa Viagem",
        "max_price": Decimal("3000"),
    }

    assert set(codes(call(conversation, **base))) == {"DOIS", "TRES"}
    assert codes(call(conversation, **base, bedrooms=3)) == ["TRES"]


def test_busca_devolve_no_maximo_dois_imoveis(conversation: Conversation) -> None:
    for index in range(5):
        make_property(f"IMV-00{index}")

    found = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert len(found.properties) == MAX_RESULTS == 2


# --- código -----------------------------------------------------------------


def test_busca_por_codigo_dispensa_os_demais_filtros(
    conversation: Conversation,
) -> None:
    make_property("IMV-001")

    found = call(conversation, code="IMV-001")

    assert codes(found) == ["IMV-001"]


def test_busca_por_codigo_inexistente_volta_vazia(conversation: Conversation) -> None:
    assert call(conversation, code="NAO-EXISTE").properties == []


# --- filtros obrigatórios ---------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [
        pytest.param({}, id="sem-nenhum-filtro"),
        pytest.param(
            {"transaction_type": TransactionType.RENT}, id="sem-bairro-e-sem-preco"
        ),
        pytest.param(
            {"transaction_type": TransactionType.RENT, "neighborhood": "Boa Viagem"},
            id="sem-preco",
        ),
        pytest.param(
            {"neighborhood": "Boa Viagem", "max_price": Decimal("3000")},
            id="sem-tipo-de-transacao",
        ),
        pytest.param(
            {
                "transaction_type": TransactionType.RENT,
                "max_price": Decimal("3000"),
            },
            id="sem-bairro",
        ),
    ],
)
def test_sem_os_filtros_obrigatorios_nenhum_imovel_e_devolvido(
    conversation: Conversation, filters: dict[str, Any]
) -> None:
    """A restrição é determinística: a IA não consegue burlar pedindo de novo."""
    make_property("IMV-001")

    with pytest.raises(ModelRetry):
        call(conversation, **filters)


def test_mensagem_de_retry_diz_o_que_falta(conversation: Conversation) -> None:
    with pytest.raises(ModelRetry) as error:
        call(conversation, transaction_type=TransactionType.RENT)

    assert "bairro" in str(error.value)
    assert "preço" in str(error.value)


# --- imóveis já recomendados ------------------------------------------------


def test_imovel_ja_recomendado_nao_volta_na_mesma_conversa(
    conversation: Conversation,
) -> None:
    recommended = make_property("JA-VISTO")
    make_property("NOVO")
    PropertyRecommendation.objects.create(
        conversation=conversation, property=recommended
    )

    found = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert codes(found) == ["NOVO"]


def test_recomendacao_de_outra_conversa_nao_filtra(
    conversation: Conversation,
) -> None:
    recommended = make_property("IMV-001")
    other = Conversation.objects.create(user_phone="+5581999999999")
    PropertyRecommendation.objects.create(conversation=other, property=recommended)

    found = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert codes(found) == ["IMV-001"]


def test_busca_por_codigo_tambem_exclui_ja_recomendados(
    conversation: Conversation,
) -> None:
    recommended = make_property("JA-VISTO")
    PropertyRecommendation.objects.create(
        conversation=conversation, property=recommended
    )

    assert call(conversation, code="JA-VISTO").properties == []


# --- corte do loop de buscas ------------------------------------------------


def test_busca_vazia_orienta_o_modelo_a_nao_tentar_de_novo(
    conversation: Conversation,
) -> None:
    """Regressão: uma lista vazia sozinha faz o modelo variar filtros em loop."""
    result = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Bairro Inexistente",
        max_price=Decimal("3000"),
    )

    assert result.properties == []
    assert "NÃO" in result.guidance
    assert "repita a busca" in result.guidance


def test_busca_com_resultado_orienta_a_apresentar(conversation: Conversation) -> None:
    make_property("IMV-001")

    result = call(
        conversation,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        max_price=Decimal("3000"),
    )

    assert "Apresente" in result.guidance


def test_teto_de_buscas_por_execucao(conversation: Conversation) -> None:
    """Depois do teto a tool para de consultar o banco, sem levantar exceção."""
    make_property("IMV-001")
    deps = AssistantDeps(conversation_id=conversation.pk)
    filters: dict[str, Any] = {
        "transaction_type": TransactionType.RENT,
        "neighborhood": "Boa Viagem",
        "max_price": Decimal("3000"),
    }

    for _ in range(MAX_SEARCHES_PER_RUN):
        assert codes(call(conversation, deps=deps, **filters)) == ["IMV-001"]

    excedente = call(conversation, deps=deps, **filters)

    assert excedente.properties == []
    assert "Limite de buscas" in excedente.guidance
    assert deps.searches_done == MAX_SEARCHES_PER_RUN


def test_filtro_invalido_nao_consome_o_teto_de_buscas(
    conversation: Conversation,
) -> None:
    deps = AssistantDeps(conversation_id=conversation.pk)

    with pytest.raises(ModelRetry):
        call(conversation, deps=deps, transaction_type=TransactionType.RENT)

    assert deps.searches_done == 0


# --- perguntas frequentes ---------------------------------------------------


def test_faq_devolve_a_base_do_arquivo() -> None:
    entries = faq_properties()

    assert len(entries) == 10
    assert all(entry.pergunta and entry.resposta for entry in entries)


def test_faq_traz_conteudo_para_o_modelo_fundamentar_a_resposta() -> None:
    perguntas = " ".join(entry.pergunta.lower() for entry in faq_properties())

    assert "documentos" in perguntas
    assert "corretagem" in perguntas


def test_faq_e_serializavel_para_o_modelo() -> None:
    assert tool_return_ta.dump_json(faq_properties())
