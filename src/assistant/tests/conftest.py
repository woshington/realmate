from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Iterator, cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import Agent, RunContext, models

from assistant.tools import AssistantDeps, _load_faq


@pytest.fixture(autouse=True)
def block_real_model_requests() -> Iterator[None]:
    """Nenhum teste pode sair para a rede: o SDK levanta erro se tentar."""
    with models.override_allow_model_requests(False):
        yield


@pytest.fixture
def make_property() -> Callable[..., SimpleNamespace]:

    def _make(code: str = "IMV-001", price: int = 2500,
              neighborhood: str = "Boa Viagem", bedrooms: int = 2,
              address: str = "Rua X, 100",
              description: str = "Ótimo imóvel") -> SimpleNamespace:
        return SimpleNamespace(
            code=code,
            price=Decimal(price),
            neighborhood=neighborhood,
            bedrooms=bedrooms,
            address=address,
            description=description,
        )

    return _make


@pytest.fixture
def make_ctx() -> Callable[..., RunContext[AssistantDeps]]:
    def _make(
        conversation_id: int = 1, **deps_kwargs: Any
    ) -> RunContext[AssistantDeps]:
        """Duck-type do ``RunContext``: as tools só leem ``ctx.deps``."""
        deps = AssistantDeps(conversation_id=conversation_id, **deps_kwargs)
        return cast(RunContext[AssistantDeps], SimpleNamespace(deps=deps))

    return _make


@pytest.fixture
def mock_property() -> Iterator[MagicMock]:
    with patch("assistant.tools.Property") as mocked:
        mocked.objects.filter.return_value.exclude.return_value = []
        yield mocked


@pytest.fixture
def mock_recommendation() -> Iterator[MagicMock]:
    with patch("assistant.tools.PropertyRecommendation") as mocked:
        mocked.objects.filter.return_value.values_list.return_value = []
        yield mocked


@pytest.fixture
def mock_faq() -> Iterator[MagicMock]:
    with patch("assistant.tools._load_faq", return_value=()) as mocked:
        yield mocked


@pytest.fixture(autouse=True)
def clear_faq_cache() -> Iterator[None]:
    _load_faq.cache_clear()
    yield
    _load_faq.cache_clear()


@pytest.fixture
def openai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Faz `build_model` montar um modelo OpenAI sem depender de credencial real.

    O patch é feito sobre o `settings` já importado por `agent.py` para valer
    apenas durante o teste, sem depender de variável de ambiente.
    """
    monkeypatch.setattr("assistant.agent.settings.USE_OLLAMA", False)
    monkeypatch.setattr("assistant.agent.settings.OPENAI_API_KEY", "sk-fake")


@pytest.fixture
def agent(openai_settings: None) -> Agent[Any, Any]:
    from assistant.agent import get_agent
    return get_agent()
