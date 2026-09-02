"""Ferramental dos testes do assistente.

O agente é testado sem rede e sem banco:

* o modelo é o ``ScriptedModel`` do próprio SDK, que devolve passos
  determinísticos (uma chamada de tool, uma mensagem final) e ainda registra o
  que o agente mandou para o modelo;
* o ORM que a tool de busca usa é substituído pela fachada ``PropertyORM``, que
  concentra os dois mocks (``Property`` e ``PropertyRecommendation``) numa API
  legível — ``orm.returns(...)``, ``orm.already_recommended(...)``.

Assim os testes falam de comportamento ("qual tool foi chamada", "quantos
imóveis voltaram") e não de encanamento.
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


# ---- Fachada sobre o ORM mockado ------------------------------------------

class PropertyORM:
    """Os dois modelos que a tool de busca consulta, num objeto só."""

    def __init__(self, property_model: MagicMock, recommendation_model: MagicMock):
        self.property = property_model
        self.recommendation = recommendation_model
        self.returns()
        self.already_recommended()

    def returns(self, *properties: Any) -> None:
        """Define o que a query de imóveis devolve."""
        self.property.objects.filter.return_value.exclude.return_value = list(properties)

    def already_recommended(self, *property_ids: int) -> None:
        """Define os imóveis já recomendados na conversa."""
        self.recommendation.objects.filter.return_value.values_list.return_value = list(
            property_ids
        )

    @property
    def searched(self) -> bool:
        return bool(self.property.objects.filter.called)

    @property
    def filters(self) -> dict[str, Any]:
        """Filtros da última query de imóveis."""
        return dict(self.property.objects.filter.call_args.kwargs)

    @property
    def excluded_ids(self) -> list[int]:
        """Ids passados para o ``.exclude()`` da última query."""
        exclude = self.property.objects.filter.return_value.exclude
        return list(exclude.call_args.kwargs["id__in"])


@pytest.fixture
def orm() -> Iterator[PropertyORM]:
    with mock.patch(f"{SEARCH_MODULE}.Property") as property_model, \
            mock.patch(f"{SEARCH_MODULE}.PropertyRecommendation") as recommendation:
        yield PropertyORM(property_model, recommendation)


@pytest.fixture
def property_stub() -> Callable[..., SimpleNamespace]:
    """Imóvel duck-typed: a tool só lê atributos, nunca toca no banco."""

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


# ---- Invocação direta de uma tool ------------------------------------------

@pytest.fixture
def call_tool() -> Callable[..., Any]:
    """Executa uma tool do jeito que o runner executa, mas sem subir o agente.

    ``@function_tool`` transforma a função num ``FunctionTool``; o corpo original
    só é alcançável por ``on_invoke_tool``, que recebe os argumentos em JSON.
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
            tool_call_id="call-teste",
            tool_arguments=raw_arguments,
        )
        invocation = tool.on_invoke_tool(context, raw_arguments)
        return asyncio.run(cast(Coroutine[Any, Any, Any], invocation))

    return _call


# ---- Execução do agente com modelo roteirizado ------------------------------

@dataclass(frozen=True)
class AgentRun:
    """Resultado de um run, com as perguntas que os testes fazem."""

    result: Any
    model: ScriptedModel
    deps: AssistantDeps

    @property
    def reply(self) -> AgentReply:
        answer: AgentReply = self.result.final_output
        return answer

    @property
    def first_call(self) -> Any:
        """Primeira chamada ao modelo — o que o agente expôs antes de qualquer tool."""
        call = self.model.first_call
        assert call is not None, "o agente não chegou a chamar o modelo"
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
    """Roda o agente real (tools, prompt e output_type de verdade) num modelo falso.

    Só ``build_model`` é trocado: tudo abaixo dele — schema das tools, execução,
    parse da resposta final — é o código de produção.
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
    """Passo do roteiro: o modelo decide chamar a tool de busca."""
    return [function_call("search_properties", arguments, call_id="call-busca")]


def calls_faq() -> list[Any]:
    """Passo do roteiro: o modelo decide consultar as perguntas frequentes."""
    return [function_call("faq_properties", {}, call_id="call-faq")]


def answers(message: str, *, recommended: list[dict[str, Any]] | None = None) -> list[Any]:
    """Passo do roteiro: o modelo devolve a resposta final no formato ``AgentReply``."""
    payload = {"message": message, "recommended_properties": recommended or []}
    return [assistant_message(json.dumps(payload, ensure_ascii=False))]


# ---- FAQ --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_faq_cache() -> Iterator[None]:
    """``_load_faq`` é cacheado em processo; o cache não pode vazar entre testes."""
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
