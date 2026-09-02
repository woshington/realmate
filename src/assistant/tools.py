import json
from decimal import Decimal
from functools import lru_cache
from typing import Iterable, Literal, Optional

from django.conf import settings
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

from assistant.schemas import FaqEntry, PropertyOutput, PropertySearchResult
from conversations.models import PropertyRecommendation
from properties.models import Property

MAX_RESULTS = 2

MAX_SEARCHES_PER_RUN = 3

NOTHING_FOUND = (
    "Nenhum imóvel encontrado com esses filtros. Diga isso ao cliente e "
    "pergunte se ele quer ajustar bairro, faixa de preço ou quartos. NÃO "
    "repita a busca com filtros que o cliente não informou."
)
SEARCH_BUDGET_SPENT = (
    "Limite de buscas para esta mensagem atingido. Responda ao cliente com o "
    "que já foi encontrado e peça novos filtros. NÃO busque de novo agora."
)
FOUND = (
    "Apresente estes imóveis ao cliente: código, bairro, preço, quartos e um "
    "resumo da descrição."
)

TransactionTypeFilter = Literal["aluguel", "venda"]


class AssistantDeps(BaseModel):
    conversation_id: int
    presented_codes: list[str] = Field(default_factory=list)
    searches_done: int = 0


def search_properties(
    ctx: RunContext[AssistantDeps],
    code: Optional[str] = None,
    transaction_type: Optional[TransactionTypeFilter] = None,
    neighborhood: Optional[str] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    bedrooms: Optional[int] = None,
) -> PropertySearchResult:
    """Busca imóveis residenciais da imobiliária em Recife/PE.

    Use esta ferramenta sempre que o cliente pedir opções de imóveis, seja por
    código, seja por características.

    Há duas formas de buscar, e elas são excludentes:

    1. **Por código**: informe apenas `code`. Os demais filtros são ignorados.
    2. **Por características**: `transaction_type`, `neighborhood` e pelo menos
       um entre `min_price` e `max_price` são OBRIGATÓRIOS. Se algum deles
       faltar, a busca é recusada e nenhum imóvel é devolvido — pergunte o dado
       que falta ao cliente antes de tentar de novo. Nunca preencha um filtro
       obrigatório com um valor que o cliente não informou explicitamente.

    A busca devolve no máximo 2 imóveis e exclui automaticamente tudo que já foi
    recomendado nesta conversa — não é preciso pedir para excluir, e um imóvel
    já apresentado nunca reaparece. O campo `guidance` da resposta diz o que
    fazer com o resultado; siga essa orientação.

    Args:
        code: Código do imóvel (ex.: "IMV-001", "C011"), quando o cliente cita
            um imóvel específico. Dispensa todos os outros filtros.
        transaction_type: "aluguel" ou "venda". Obrigatório se `code` não for
            informado.
        neighborhood: Bairro em Recife (ex.: "Boa Viagem"). Obrigatório se
            `code` não for informado.
        min_price: Preço mínimo, em reais. Use quando o cliente der um piso
            ("a partir de 2000").
        max_price: Preço máximo, em reais. Use quando o cliente der um teto
            ("até 3000"). Ao menos um entre `min_price` e `max_price` é
            obrigatório se `code` não for informado.
        bedrooms: Número exato de quartos. Sempre opcional.

    Returns:
        Os imóveis encontrados e uma orientação (`guidance`) sobre o que fazer
        em seguida.
    """

    if ctx.deps.searches_done >= MAX_SEARCHES_PER_RUN:
        return PropertySearchResult(guidance=SEARCH_BUDGET_SPENT)

    already_recommended = list(
        PropertyRecommendation.objects.filter(
            conversation_id=ctx.deps.conversation_id
        ).values_list("property_id", flat=True)
    )

    if code:
        ctx.deps.searches_done += 1
        found = Property.objects.filter(code=code).exclude(id__in=already_recommended)
        return _to_result(found[:MAX_RESULTS], ctx.deps)

    missing = []
    if transaction_type is None:
        missing.append("tipo de transação (aluguel ou venda)")
    if neighborhood is None:
        missing.append("bairro")
    if min_price is None and max_price is None:
        missing.append("preço mínimo, máximo ou faixa")

    if missing:
        raise ModelRetry(
            "Não é possível buscar sem estes filtros: "
            + "; ".join(missing)
            + ". Pergunte ao cliente antes de tentar de novo."
        )

    params: dict[str, object] = {
        "transaction_type": transaction_type,
        "neighborhood__iexact": neighborhood,
    }
    if min_price is not None:
        params["price__gte"] = min_price
    if max_price is not None:
        params["price__lte"] = max_price
    if bedrooms is not None:
        params["bedrooms"] = bedrooms

    ctx.deps.searches_done += 1
    found = Property.objects.filter(**params).exclude(id__in=already_recommended)
    return _to_result(found[:MAX_RESULTS], ctx.deps)


def faq_properties() -> list[FaqEntry]:
    """Consulta a base de perguntas frequentes sobre a imobiliária Realmate.

    Use esta ferramenta para dúvidas sobre a Realmate, como documentos necessários,
    taxas, prazos, horários de atendimento, formas de pagamento, visitas e contratos.

    A ferramenta retorna todas as perguntas e respostas disponíveis na base.

    Responda ao cliente usando APENAS as informações presentes nas entradas retornadas.
    Nunca deduza, invente ou complemente informações que não estejam na base.

    Caso a base não contenha informações suficientes para responder à dúvida do cliente,
    informe que não encontrou a resposta para a dúvida e sugira que ele entre em contato
    diretamente com a imobiliária.

    Returns:
        Todas as entradas da base, cada uma contendo `pergunta` e `resposta`.
    """

    return list(_load_faq())


@lru_cache(maxsize=1)
def _load_faq() -> tuple[FaqEntry, ...]:
    with open(settings.FAQ_JSON_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return tuple(FaqEntry.model_validate(entry) for entry in raw)


def _to_result(found: Iterable[Property], deps: AssistantDeps) -> PropertySearchResult:
    properties = [_to_output(property) for property in found]
    deps.presented_codes.extend(property.code for property in properties)
    return PropertySearchResult(
        properties=properties,
        guidance=FOUND if properties else NOTHING_FOUND,
    )


def _to_output(property: Property) -> PropertyOutput:
    return PropertyOutput(
        code=property.code,
        price=int(property.price),
        neighborhood=property.neighborhood,
        bedrooms=property.bedrooms,
        address=property.address,
        description=property.description,
    )
