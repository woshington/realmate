"""Property search tool.

The rules tested here are the ones that stop the assistant from inventing a
property: no search without the required filters, an already shown property does
not come back, and every answer carries at most two properties.
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


class TestRequiredFilters:
    """Without the required data the tool refuses and tells the model to ask."""

    @pytest.mark.parametrize(
        ("filters", "missing_data"),
        [
            ({}, "tipo de transação"),
            ({}, "bairro"),
            ({}, "preço"),
            ({"neighborhood": "Boa Viagem", "max_price": 3000}, "tipo de transação"),
            ({"transaction_type": "aluguel", "max_price": 3000}, "bairro"),
            ({"transaction_type": "aluguel", "neighborhood": "Boa Viagem"}, "preço"),
        ],
    )
    def test_guides_the_model_to_ask_for_the_missing_data(
        self, call_tool: CallTool, orm: PropertyORM,
        filters: dict[str, Any], missing_data: str,
    ) -> None:
        result = search(call_tool, **filters)

        assert missing_data in result.guidance
        assert "Pergunte ao cliente" in result.guidance

    def test_does_not_query_the_database_when_a_filter_is_missing(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(call_tool, transaction_type="aluguel", neighborhood="Boa Viagem")

        assert orm.searched is False

    def test_returns_no_property_when_a_filter_is_missing(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub())

        result = search(call_tool, transaction_type="aluguel")

        assert result.properties == []

    def test_a_refused_search_does_not_spend_the_search_budget(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        search(call_tool, deps, transaction_type="aluguel")

        assert deps.searches_done == 0

    @pytest.mark.parametrize("price", [{"min_price": 1000}, {"max_price": 3000}])
    def test_one_price_bound_already_satisfies_the_required_filter(
        self, call_tool: CallTool, orm: PropertyORM, price: dict[str, int],
    ) -> None:
        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            **price,
        )

        assert orm.searched is True

    def test_the_code_waives_every_other_filter(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(call_tool, code="IMV-001")

        assert orm.filters == {"code": "IMV-001"}


class TestNoPropertyRepeats:
    """A property already presented in the conversation must not come back."""

    def test_excludes_the_properties_already_recommended_in_the_conversation(
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

    def test_queries_the_recommendations_of_the_right_conversation(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=42)

        search(call_tool, deps, code="IMV-001")

        orm.recommendation.objects.filter.assert_called_once_with(conversation_id=42)

    def test_excludes_on_a_search_by_code_as_well(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        orm.already_recommended(7)

        search(call_tool, code="IMV-001")

        assert orm.excluded_ids == [7]

    def test_records_the_presented_codes_for_the_task(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        orm.returns(property_stub(code="IMV-007"))

        search(call_tool, deps, code="IMV-007")

        assert deps.presented_codes == ["IMV-007"]

    def test_accumulates_the_codes_of_successive_searches(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        orm.returns(property_stub(code="IMV-001"))
        search(call_tool, deps, code="IMV-001")
        orm.returns(property_stub(code="IMV-002"))
        search(call_tool, deps, code="IMV-002")

        assert deps.presented_codes == ["IMV-001", "IMV-002"]


class TestResultLimit:
    """At most two properties per answer."""

    def test_returns_at_most_two_properties(
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

    def test_presents_the_first_two_of_the_query(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(5)])

        result = search(call_tool, code="IMV-0")

        assert [property_.code for property_ in result.properties] == ["IMV-0", "IMV-1"]

    def test_the_limit_also_applies_to_the_presented_codes(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(5)])

        search(call_tool, deps, code="IMV-0")

        assert len(deps.presented_codes) == MAX_RESULTS


class TestSearchBudget:
    """A single customer message must not turn into a catalog sweep."""

    def test_refuses_the_search_once_the_budget_is_spent(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        result = search(call_tool, deps, code="IMV-001")

        assert result.guidance == SEARCH_BUDGET_SPENT
        assert result.properties == []
        assert orm.searched is False

    def test_does_not_increment_the_counter_past_the_limit(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1, searches_done=MAX_SEARCHES_PER_RUN)

        search(call_tool, deps, code="IMV-001")

        assert deps.searches_done == MAX_SEARCHES_PER_RUN

    def test_every_effective_search_consumes_one_unit(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        deps = AssistantDeps(conversation_id=1)

        search(call_tool, deps, code="IMV-001")
        search(call_tool, deps, code="IMV-002")

        assert deps.searches_done == 2


class TestQueryFilters:
    """What the customer told us becomes a filter; what they did not, does not."""

    def test_builds_the_query_with_every_filter_provided(
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

    def test_bedrooms_is_optional_and_stays_out_of_the_query_when_absent(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        search(
            call_tool,
            transaction_type="aluguel",
            neighborhood="Boa Viagem",
            max_price=3000,
        )

        assert "bedrooms" not in orm.filters

    def test_the_range_given_by_the_customer_becomes_a_query_filter(
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


class TestAnswerGuidance:
    """The ``guidance`` field is what tells the model what to do next."""

    def test_guides_the_model_to_present_when_something_is_found(
        self, call_tool: CallTool, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub())

        result = search(call_tool, code="IMV-001")

        assert result.guidance == FOUND

    def test_guides_the_model_to_be_transparent_when_nothing_is_found(
        self, call_tool: CallTool, orm: PropertyORM,
    ) -> None:
        orm.returns()

        result = search(call_tool, code="IMV-404")

        assert result.guidance == NOTHING_FOUND
        assert result.properties == []

    def test_converts_the_property_to_the_answer_format(
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

        found = search(call_tool, code="IMV-001").properties[0]

        assert (found.code, found.price, found.bedrooms) == ("IMV-001", 2500, 2)
        assert found.neighborhood == "Boa Viagem"
        assert found.address == "Rua dos Navegantes, 150"
        assert found.description == "Apartamento com varanda"
