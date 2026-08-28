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
