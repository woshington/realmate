from typing import Iterable, Optional
from decimal import Decimal

from agents import function_tool, RunContextWrapper

from assistant.schemas import PropertySearchResult, PropertyOutput
from assistant.tools import AssistantDeps, TransactionTypeFilter, SEARCH_BUDGET_SPENT, NOTHING_FOUND, FOUND
from conversations.models import PropertyRecommendation
from properties.models import Property

MAX_RESULTS = 2
MAX_SEARCHES_PER_RUN = 3


@function_tool
def search_properties(
    ctx: RunContextWrapper[AssistantDeps],
    code: Optional[str] = None,
    transaction_type: Optional[TransactionTypeFilter] = None,
    neighborhood: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
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
    if ctx.context.searches_done >= MAX_SEARCHES_PER_RUN:
        return PropertySearchResult(
            guidance=SEARCH_BUDGET_SPENT,
        )

    already_recommended = list(
        PropertyRecommendation.objects.filter(
            conversation_id=ctx.context.conversation_id,
        ).values_list(
            "property_id",
            flat=True,
        )
    )

    if code:
        ctx.context.searches_done += 1

        found = (
            Property.objects
            .filter(code=code)
            .exclude(id__in=already_recommended)
        )

        return _to_result(
            found[:MAX_RESULTS],
            ctx.context,
        )

    missing = []

    if transaction_type is None:
        missing.append("tipo de transação (aluguel ou venda)")

    if neighborhood is None:
        missing.append("bairro")

    if min_price is None and max_price is None:
        missing.append("preço mínimo, máximo ou faixa")

    if missing:
        return PropertySearchResult(
            guidance=(
                "Não é possível buscar sem estes filtros: "
                + "; ".join(missing)
                + ". Pergunte ao cliente antes de tentar de novo."
            )
        )

    params: dict[str, object] = {
        "transaction_type": transaction_type,
        "neighborhood__iexact": neighborhood,
    }

    if min_price is not None:
        params["price__gte"] = float(str(min_price))

    if max_price is not None:
        params["price__lte"] = float(str(max_price))

    if bedrooms is not None:
        params["bedrooms"] = bedrooms

    ctx.context.searches_done += 1

    found = (
        Property.objects
        .filter(**params)
        .exclude(id__in=already_recommended)
    )

    return _to_result(
        found[:MAX_RESULTS],
        ctx.context,
    )

def _to_result(
    found: Iterable[Property],
    deps: AssistantDeps,
) -> PropertySearchResult:

    properties = [
        _to_output(property)
        for property in found
    ]

    deps.presented_codes.extend(
        property.code
        for property in properties
    )

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