"""Test tooling for the assistant.

The agent is tested without network and without a database:

* the model is the SDK's own ``ScriptedModel``, which returns deterministic
  steps (a tool call, a final message) and also records what the agent sent to
  the model;
* the ORM used by the search tool is replaced by the ``PropertyORM`` facade,
  which gathers both mocks (``Property`` and ``PropertyRecommendation``) behind
  a readable API — ``orm.returns(...)``, ``orm.already_recommended(...)``.

That way the tests talk about behavior ("which tool was called", "how many
properties came back") instead of plumbing.
"""

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Iterator, cast
from unittest import mock
from unittest.mock import MagicMock

import pytest
from agents import Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from assistant import agent as agent_module
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps
from assistant.tools.faq_tools import _load_faq

SEARCH_MODULE = "assistant.tools.search_properties_tools"


# ---- Facade over the mocked ORM --------------------------------------------

class PropertyORM:
    """The two models the search tool queries, behind a single object."""

    def __init__(self, property_model: MagicMock, recommendation_model: MagicMock):
        self.property = property_model
        self.recommendation = recommendation_model
        self.returns()
        self.already_recommended()

    def returns(self, *properties: Any) -> None:
        """Define what the property query returns."""
        self.property.objects.filter.return_value.exclude.return_value = list(properties)

    def already_recommended(self, *property_ids: int) -> None:
        """Define the properties already recommended in the conversation."""
        self.recommendation.objects.filter.return_value.values_list.return_value = list(
            property_ids
        )

    @property
    def searched(self) -> bool:
        return bool(self.property.objects.filter.called)

    @property
    def filters(self) -> dict[str, Any]:
        """Filters of the last property query."""
        return dict(self.property.objects.filter.call_args.kwargs)

    @property
    def excluded_ids(self) -> list[int]:
        """Ids passed to the ``.exclude()`` of the last query."""
        exclude = self.property.objects.filter.return_value.exclude
        return list(exclude.call_args.kwargs["id__in"])


@pytest.fixture
def orm() -> Iterator[PropertyORM]:
    with mock.patch(f"{SEARCH_MODULE}.Property") as property_model, \
            mock.patch(f"{SEARCH_MODULE}.PropertyRecommendation") as recommendation:
        yield PropertyORM(property_model, recommendation)


@pytest.fixture
def property_stub() -> Callable[..., SimpleNamespace]:
    """Duck-typed property: the tool only reads attributes, never hits the database."""

    def _make(
        code: str = "IMV-001",
        price: str = "2500",
        neighborhood: str = "Boa Viagem",
        bedrooms: int = 2,
        address: str = "Rua dos Navegantes, 150",
        description: str = "Apartamento com varanda",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            code=code,
            price=Decimal(price),
            neighborhood=neighborhood,
            bedrooms=bedrooms,
            address=address,
            description=description,
        )

    return _make


# ---- Direct invocation of a tool -------------------------------------------

@pytest.fixture
def call_tool() -> Callable[..., Any]:
    """Run a tool the way the runner runs it, but without starting the agent.

    ``@function_tool`` turns the function into a ``FunctionTool``; the original
    body is only reachable through ``on_invoke_tool``, which takes its arguments
    as JSON.
    """

    def _call(
        tool: FunctionTool,
        deps: AssistantDeps | None = None,
        /,
        **arguments: Any,
    ) -> Any:
        raw_arguments = json.dumps(arguments)
        context: ToolContext[Any] = ToolContext(
            context=deps if deps is not None else AssistantDeps(conversation_id=1),
            tool_name=tool.name,
            tool_call_id="call-test",
            tool_arguments=raw_arguments,
        )
        invocation = tool.on_invoke_tool(context, raw_arguments)
        return asyncio.run(cast(Coroutine[Any, Any, Any], invocation))

    return _call


# ---- Running the agent with a scripted model --------------------------------

@dataclass(frozen=True)
class AgentRun:
    """Result of a run, exposing the questions the tests ask."""

    result: Any
    model: ScriptedModel
    deps: AssistantDeps

    @property
    def reply(self) -> AgentReply:
        answer: AgentReply = self.result.final_output
        return answer

    @property
    def first_call(self) -> Any:
        """First call to the model — what the agent exposed before any tool."""
        call = self.model.first_call
        assert call is not None, "the agent never called the model"
        return call

    @property
    def tools_called(self) -> list[str]:
        return [
            item.raw_item.name
            for item in self.result.new_items
            if isinstance(item, ToolCallItem)
            and isinstance(item.raw_item, ResponseFunctionToolCall)
        ]

    @property
    def tool_outputs(self) -> list[Any]:
        return [
            item.output
            for item in self.result.new_items
            if isinstance(item, ToolCallOutputItem)
        ]


@pytest.fixture
def run_agent(orm: PropertyORM) -> Callable[..., AgentRun]:
    """Run the real agent (real tools, prompt and output_type) on a fake model.

    Only ``build_model`` is swapped: everything below it — tool schemas,
    execution, parsing of the final answer — is production code.
    """

    def _run(
        *steps: Any,
        message: str = "Olá",
        deps: AssistantDeps | None = None,
    ) -> AgentRun:
        model = ScriptedModel(steps)
        run_deps = deps if deps is not None else AssistantDeps(conversation_id=1)

        with mock.patch.object(agent_module, "build_model", return_value=model):
            result = Runner.run_sync(
                starting_agent=agent_module.get_agent(),
                input=message,
                context=run_deps,
            )

        model.assert_complete()
        return AgentRun(result=result, model=model, deps=run_deps)

    return _run


def calls_search(**arguments: Any) -> list[Any]:
    """Script step: the model decides to call the search tool."""
    return [function_call("search_properties", arguments, call_id="call-search")]


def calls_faq() -> list[Any]:
    """Script step: the model decides to consult the frequently asked questions."""
    return [function_call("faq_properties", {}, call_id="call-faq")]


def answers(message: str, *, recommended: list[dict[str, Any]] | None = None) -> list[Any]:
    """Script step: the model returns the final answer in the ``AgentReply`` format."""
    payload = {"message": message, "recommended_properties": recommended or []}
    return [assistant_message(json.dumps(payload, ensure_ascii=False))]


# ---- FAQ --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_faq_cache() -> Iterator[None]:
    """``_load_faq`` is cached in-process; the cache must not leak between tests."""
    _load_faq.cache_clear()
    yield
    _load_faq.cache_clear()


@pytest.fixture
def faq_file(tmp_path: Path, settings: Any) -> Callable[..., Path]:
    def _write(*entries: dict[str, str]) -> Path:
        file_path = tmp_path / "perguntas_frequentes.json"
        file_path.write_text(
            json.dumps(list(entries), ensure_ascii=False),
            encoding="utf-8",
        )
        settings.FAQ_JSON_PATH = str(file_path)
        return file_path

    return _write
