import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pytest_django.fixtures import Settings

from assistant import FALLBACK_MESSAGE
from assistant.schemas import AgentReply, PropertyOutput
from assistant.tools import AssistantDeps
from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from conversations.tasks import process_conversation, schedule_conversation_processing

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


# ---- Helpers ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache() -> Iterator[None]:
    """The task lock lives in Redis, so it has to be reset around every test."""
    cache.clear()
    yield
    cache.clear()


def create_conversation(user_phone: str = PHONE) -> Conversation:
    return Conversation.objects.create(user_phone=user_phone)


def create_message(
    conversation: Conversation,
    timestamp: datetime,
    role: str = MessageRole.CUSTOMER,
    content: str = "Olá",
) -> Message:
    return Message.objects.create(
        external_id=uuid.uuid4(),
        conversation=conversation,
        content=content,
        role=role,
        timestamp=timestamp,
    )


def property_output(code: str) -> PropertyOutput:
    return PropertyOutput(
        code=code,
        price=2000,
        neighborhood="Boa Viagem",
        bedrooms=2,
        address="Rua X, 100",
        description="Ótimo imóvel",
    )


def lock_key_for(conversation_id: int, trigger_message_id: int) -> str:
    return f"lock:{conversation_id}-{trigger_message_id}"


def agent_replying_with(
    reply: AgentReply, presented_codes: list[str] | None = None,
) -> MagicMock:
    # Mirrors the agent's real side effect: running the search tool fills
    # deps.presented_codes before the run returns.
    def fake_run_sync(
        *args: Any, deps: AssistantDeps, **kwargs: Any
    ) -> SimpleNamespace:
        if presented_codes:
            deps.presented_codes.extend(presented_codes)
        return SimpleNamespace(output=reply)

    fake_agent = mock.MagicMock()
    fake_agent.run_sync.side_effect = fake_run_sync
    return fake_agent


def agent_raising(exception: Exception) -> MagicMock:
    fake_agent = mock.MagicMock()
    fake_agent.run_sync.side_effect = exception
    return fake_agent


def patched_agent(fake_agent: MagicMock) -> AbstractContextManager[MagicMock]:
    return mock.patch("conversations.tasks.agent.get_agent", return_value=fake_agent)


def patched_history() -> AbstractContextManager[MagicMock]:
    return mock.patch("conversations.tasks.to_model_messages", return_value=[])


# ---- schedule_conversation_processing ------------------------------------------

class TestScheduleConversationProcessing:
    def test_enqueues_process_conversation_with_the_configured_debounce(
        self, settings: Settings,
    ) -> None:
        settings.DEBOUNCE_WINDOW_SECONDS = 30

        with mock.patch.object(process_conversation, "apply_async") as mock_apply_async:
            schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

        mock_apply_async.assert_called_once_with(
            kwargs={"conversation_id": 1, "trigger_message_id": 2},
            countdown=30,
        )


# ---- process_conversation: locking ----------------------------------------------

class TestProcessConversationLocking:
    def test_skips_processing_when_the_lock_is_already_held(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        cache.set(lock_key_for(conversation.pk, trigger.pk), "true", 15)

        with mock.patch("conversations.tasks.agent.get_agent") as mock_get_agent:
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        mock_get_agent.assert_not_called()
        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 0

    def test_releases_the_lock_after_a_successful_run(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        reply = AgentReply(message="Oi!", recommended_properties=[])

        with patched_agent(agent_replying_with(reply)), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assert cache.get(lock_key_for(conversation.pk, trigger.pk)) is None

    def test_releases_the_lock_when_the_message_was_superseded(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        create_message(conversation, NOW + timedelta(minutes=1))

        process_conversation(
            conversation_id=conversation.pk, trigger_message_id=trigger.pk,
        )

        assert cache.get(lock_key_for(conversation.pk, trigger.pk)) is None

    def test_releases_the_lock_even_when_the_fallback_is_used(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        failing_agent = agent_raising(UsageLimitExceeded("limite atingido"))

        with patched_agent(failing_agent), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assert cache.get(lock_key_for(conversation.pk, trigger.pk)) is None


# ---- process_conversation: superseded messages ----------------------------------

class TestProcessConversationSupersededByNewerMessage:
    def test_does_not_call_the_agent_when_a_newer_customer_message_exists(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        create_message(conversation, NOW + timedelta(minutes=1))

        with mock.patch("conversations.tasks.agent.get_agent") as mock_get_agent:
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        mock_get_agent.assert_not_called()
        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 0

    def test_is_not_superseded_by_a_newer_message_from_another_conversation(
        self,
    ) -> None:
        conversation = create_conversation(user_phone=PHONE)
        other_conversation = create_conversation(user_phone=OTHER_PHONE)
        trigger = create_message(conversation, NOW)
        create_message(other_conversation, NOW + timedelta(minutes=1))
        reply = AgentReply(message="Oi!", recommended_properties=[])

        with patched_agent(agent_replying_with(reply)), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 1


# ---- process_conversation: success path -----------------------------------------

class TestProcessConversationSuccess:
    def test_stores_the_agent_reply_as_an_assistant_message(
        self, settings: Settings,
    ) -> None:
        settings.AGENT_HISTORY_MESSAGE_LIMIT = 10
        settings.AGENT_REQUEST_LIMIT = 5
        conversation = create_conversation()
        trigger = create_message(conversation, NOW, content="Quero um apartamento")
        reply = AgentReply(
            message="Temos ótimas opções para você.", recommended_properties=[],
        )

        with patched_agent(agent_replying_with(reply)), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assistant_message = Message.objects.get(role=MessageRole.ASSISTANT)
        assert assistant_message.content == reply.message
        assert assistant_message.conversation == conversation
        assert assistant_message.timestamp == trigger.timestamp

    def test_uses_the_reply_codes_for_the_recommendations(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        reply = AgentReply(
            message="Encontrei um ótimo imóvel.",
            recommended_properties=[property_output("IMV-001")],
        )

        with patched_agent(agent_replying_with(reply, presented_codes=["IMV-099"])), \
                patched_history(), \
                mock.patch(
                    "conversations.tasks.add_recommendations",
                ) as mock_add_recommendations:
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        mock_add_recommendations.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-001"],
        )

    def test_falls_back_to_the_codes_presented_by_the_tool_when_the_reply_has_none(
        self,
    ) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        reply = AgentReply(
            message="Não encontrei nada além do que já te mostrei.",
            recommended_properties=[],
        )

        with patched_agent(agent_replying_with(reply, presented_codes=["IMV-099"])), \
                patched_history(), \
                mock.patch(
                    "conversations.tasks.add_recommendations",
                ) as mock_add_recommendations:
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        mock_add_recommendations.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-099"],
        )

    def test_recommends_nothing_when_both_the_reply_and_the_deps_are_empty(
        self,
    ) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        reply = AgentReply(message="Não encontrei nada.", recommended_properties=[])

        with patched_agent(agent_replying_with(reply)), \
                patched_history(), \
                mock.patch(
                    "conversations.tasks.add_recommendations",
                ) as mock_add_recommendations:
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        mock_add_recommendations.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=[],
        )


# ---- process_conversation: fallback ---------------------------------------------

class TestProcessConversationFallback:
    def test_uses_the_fallback_message_when_the_usage_limit_is_exceeded(self) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        failing_agent = agent_raising(UsageLimitExceeded("limite atingido"))

        with patched_agent(failing_agent), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assistant_message = Message.objects.get(role=MessageRole.ASSISTANT)
        assert assistant_message.content == FALLBACK_MESSAGE

    def test_uses_the_fallback_message_when_the_model_behaves_unexpectedly(
        self,
    ) -> None:
        conversation = create_conversation()
        trigger = create_message(conversation, NOW)
        failing_agent = agent_raising(UnexpectedModelBehavior("resposta inválida"))

        with patched_agent(failing_agent), patched_history():
            process_conversation(
                conversation_id=conversation.pk, trigger_message_id=trigger.pk,
            )

        assistant_message = Message.objects.get(role=MessageRole.ASSISTANT)
        assert assistant_message.content == FALLBACK_MESSAGE
