"""Test tooling for the conversation app.

The agent has its own coverage in ``assistant/tests``. Here it is a stand-in:
what the task has to guarantee is what happens *around* the AI — lock, debounce,
answer persistence and fallback. ``FakeRunner`` replaces the SDK ``Runner`` with
an object that records the call and returns whatever the test tells it to.
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
EXTERNAL_CONVERSATION_ID = "conv-test"


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
    """Stand-in for the SDK ``Runner``, exposing what the task observes around it."""

    def __init__(self, runner_mock: MagicMock, agent_factory: MagicMock):
        self.mock = runner_mock
        self.agent_factory = agent_factory
        self.replies_with(reply_with())

    def replies_with(self, reply: AgentReply, presented: list[str] | None = None) -> None:
        """Make the run end with ``reply``.

        ``presented`` reproduces the real side effect of the search tool, which
        records in ``deps.presented_codes`` everything shown to the customer.
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
        """Building the agent already costs: it is the sign the AI entered the path."""
        return bool(self.agent_factory.called)

    @property
    def input_sent(self) -> list[Any]:
        """Messages sent to the model on the last run."""
        return list(self.mock.run_sync.call_args.kwargs["input"])

    @property
    def external_conversation_sent(self) -> Any:
        """Provider conversation the run was attached to.

        It is what lets the model see the history the task no longer sends.
        """
        return self.mock.run_sync.call_args.kwargs["conversation_id"]

    @property
    def deps_used(self) -> AssistantDeps:
        deps: AssistantDeps = self.mock.run_sync.call_args.kwargs["context"]
        return deps


@pytest.fixture
def runner() -> Iterator[FakeRunner]:
    """Isolate the task from the AI: the agent is not built, the SDK is not called."""

    with mock.patch("conversations.tasks.Runner") as runner_mock, \
            mock.patch("conversations.tasks.agent.get_agent") as agent_factory:
        yield FakeRunner(runner_mock, agent_factory)
