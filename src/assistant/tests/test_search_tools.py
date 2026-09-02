from decimal import Decimal
from types import SimpleNamespace
from typing import Iterator, cast
from unittest import mock
from unittest.mock import MagicMock

import pytest
from pydantic_ai import ModelRetry, RunContext

from assistant.tools import (
    FOUND,
    MAX_RESULTS,
    MAX_SEARCHES_PER_RUN,
    NOTHING_FOUND,
    SEARCH_BUDGET_SPENT,
    AssistantDeps,
    search_properties,
)


# ---- Helpers ---------------------------------------------------------------

def context_with(deps: AssistantDeps) -> RunContext[AssistantDeps]:
    """Duck-type do ``RunContext``: as tools só leem ``ctx.deps``."""
    return cast(RunContext[AssistantDeps], SimpleNamespace(deps=deps))


def property_stub(code: str = "IMV-001", price: int = 2000,
                  neighborhood: str = "Boa Viagem", bedrooms: int = 2,
                  address: str = "Rua X, 100",
                  description: str = "Ótimo imóvel") -> SimpleNamespace:
    return SimpleNamespace(
        code=code, price=price, neighborhood=neighborhood,
        bedrooms=bedrooms, address=address, description=description,
    )


@pytest.fixture
def mock_property() -> Iterator[MagicMock]:
    with mock.patch("assistant.tools.Property") as mock_property_cls:
        yield mock_property_cls


@pytest.fixture
def mock_recommendation() -> Iterator[MagicMock]:
    with mock.patch("assistant.tools.PropertyRecommendation") as mock_recommendation_cls:
        mock_recommendation_cls.objects.filter.return_value.values_list.return_value = []
        yield mock_recommendation_cls


def stub_found_properties(
    mock_property: MagicMock, properties: list[SimpleNamespace],
) -> None:
    mock_property.objects.filter.return_value.exclude.return_value = properties


# ---- search_properties: orçamento de buscas --------------------------------

class TestSearchBudget:
    def test_refuses_to_search_once_budget_is_spent(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        result = search_properties(context_with(deps), code="IMV-001")

        assert result.guidance == SEARCH_BUDGET_SPENT
        assert result.properties == []
        mock_property.objects.filter.assert_not_called()

    def test_does_not_increment_searches_done_once_budget_is_spent(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        search_properties(context_with(deps), code="IMV-001")

        assert deps.searches_done == MAX_SEARCHES_PER_RUN


# ---- search_properties: busca por código -----------------------------------

class TestSearchByCode:
    def test_filters_only_by_code_ignoring_other_params(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [property_stub(code="IMV-001")])

        search_properties(
            context_with(deps),
            code="IMV-001",
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
        )

        mock_property.objects.filter.assert_called_once_with(code="IMV-001")

    def test_excludes_properties_already_recommended_in_conversation(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        mock_recommendation.objects.filter.return_value.values_list.return_value = [5, 9]
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [property_stub()])

        search_properties(context_with(deps), code="IMV-001")

        mock_recommendation.objects.filter.assert_called_once_with(conversation_id=1)
        mock_property.objects.filter.return_value.exclude.assert_called_once_with(
            id__in=[5, 9]
        )

    def test_increments_searches_done(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [property_stub()])

        search_properties(context_with(deps), code="IMV-001")

        assert deps.searches_done == 1


# ---- search_properties: filtros obrigatórios --------------------------------

class TestSearchRequiredFilters:
    def test_raises_retry_when_all_required_filters_are_missing(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        with pytest.raises(ModelRetry):
            search_properties(context_with(deps))

    def test_raises_retry_when_price_range_is_missing(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        with pytest.raises(ModelRetry):
            search_properties(
                context_with(deps),
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
            )

    def test_does_not_count_a_refused_search_against_the_budget(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        with pytest.raises(ModelRetry):
            search_properties(context_with(deps))

        assert deps.searches_done == 0

    def test_accepts_only_min_price_as_satisfying_the_price_filter(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [])

        search_properties(
            context_with(deps),
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=Decimal("1000"),
        )

        mock_property.objects.filter.assert_called_once_with(
            transaction_type="aluguel",
            neighborhood__iexact="Boa Viagem",
            price__gte=Decimal("1000"),
        )


# ---- search_properties: busca por características ---------------------------

class TestSearchByCharacteristics:
    def test_builds_filters_from_all_given_params(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [])

        search_properties(
            context_with(deps),
            transaction_type="venda",
            neighborhood="Boa Viagem",
            min_price=Decimal("100000"),
            max_price=Decimal("300000"),
            bedrooms=3,
        )

        mock_property.objects.filter.assert_called_once_with(
            transaction_type="venda",
            neighborhood__iexact="Boa Viagem",
            price__gte=Decimal("100000"),
            price__lte=Decimal("300000"),
            bedrooms=3,
        )

    def test_limits_results_to_max_results(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(
            mock_property,
            [property_stub(code=f"IMV-{i}") for i in range(5)],
        )

        result = search_properties(
            context_with(deps),
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=Decimal("1000"),
        )

        assert len(result.properties) == MAX_RESULTS

    def test_records_presented_codes_on_deps(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [property_stub(code="IMV-007")])

        search_properties(
            context_with(deps),
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=Decimal("1000"),
        )

        assert deps.presented_codes == ["IMV-007"]

    def test_returns_found_guidance_when_properties_are_returned(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [property_stub()])

        result = search_properties(
            context_with(deps),
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=Decimal("1000"),
        )

        assert result.guidance == FOUND

    def test_returns_nothing_found_guidance_when_no_properties_match(
        self, mock_property: MagicMock, mock_recommendation: MagicMock,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        stub_found_properties(mock_property, [])

        result = search_properties(
            context_with(deps),
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            min_price=Decimal("1000"),
        )

        assert result.guidance == NOTHING_FOUND
        assert result.properties == []


