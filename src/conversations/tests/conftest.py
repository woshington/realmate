"""Ferramental dos testes de conversa.

O agente já tem cobertura própria em ``assistant/tests``. Aqui ele é um dublê:
o que a task precisa garantir é o que acontece *em volta* da IA — lock, debounce,
persistência da resposta e fallback. ``FakeRunner`` substitui o ``Runner`` do SDK
por um objeto que registra a chamada e devolve o que o teste mandar.
"""

from datetime import datetime, timezone
from typing import Any, Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest

from assistant.schemas import AgentReply, PropertyOutput
from assistant.tools import AssistantDeps

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


def property_output(code: str = "IMV-001") -> PropertyOutput:
    return PropertyOutput(
        code=code,
        price=2500,
        neighborhood="Boa Viagem",
        bedrooms=2,
        address="Rua dos Navegantes, 150",
        description="Apartamento com varanda",
    )


def reply_with(message: str = "Olá!", *codes: str) -> AgentReply:
    return AgentReply(
        message=message,
        recommended_properties=[property_output(code) for code in codes],
    )


class FakeRunner:
    """Dublê do ``Runner`` do SDK, com o que a task observa em volta dele."""

    def __init__(self, runner_mock: MagicMock, agent_factory: MagicMock):
        self.mock = runner_mock
        self.agent_factory = agent_factory
        self.replies_with(reply_with())

    def replies_with(self, reply: AgentReply, presented: list[str] | None = None) -> None:
        """Faz o run terminar com ``reply``.

        ``presented`` reproduz o efeito colateral real da tool de busca, que
        anota em ``deps.presented_codes`` tudo que foi mostrado ao cliente.
        """

        def _run_sync(*_: Any, context: AssistantDeps, **__: Any) -> Any:
            if presented:
                context.presented_codes.extend(presented)
            return mock.Mock(final_output=reply)

        self.mock.run_sync.side_effect = _run_sync

    def fails_with(self, error: Exception) -> None:
        self.mock.run_sync.side_effect = error

    @property
    def ran(self) -> bool:
        return bool(self.mock.run_sync.called)

    @property
    def agent_was_built(self) -> bool:
        """Montar o agente já custa: é o sinal de que a IA entrou no caminho."""
        return bool(self.agent_factory.called)

    @property
    def input_sent(self) -> list[Any]:
        """Mensagens enviadas ao modelo na última execução."""
        return list(self.mock.run_sync.call_args.kwargs["input"])

    @property
    def deps_used(self) -> AssistantDeps:
        deps: AssistantDeps = self.mock.run_sync.call_args.kwargs["context"]
        return deps


@pytest.fixture
def runner() -> Iterator[FakeRunner]:
    """Isola a task da IA: nem o agente é montado, nem o SDK é chamado."""

    with mock.patch("conversations.tasks.Runner") as runner_mock, \
            mock.patch("conversations.tasks.agent.get_agent") as agent_factory:
        yield FakeRunner(runner_mock, agent_factory)
