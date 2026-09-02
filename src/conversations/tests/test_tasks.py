"""The task that answers a conversation.

What this task protects is not the quality of the answer — that belongs to the
agent — but what happens around it: waiting for the customer to stop typing
(debounce), never answering the same message twice (lock), and never leaving the
customer without an answer when the AI fails (fallback).
"""

from datetime import timedelta
from typing import Any, Callable
from unittest import mock
from uuid import uuid4

import pytest
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError

from django.core.cache import cache
from django.utils import timezone

from assistant import FALLBACK_MESSAGE
from conversations.enums import ConversationStatus, MessageRole
from conversations.models import Conversation, Message
from conversations.services import register_message
from conversations.tasks import (
    expire_inactive_conversations,
    process_conversation,
    schedule_conversation_processing,
)

from .conftest import (
    EXTERNAL_CONVERSATION_ID,
    NOW,
    OTHER_PHONE,
    PHONE,
    FakeRunner,
    reply_with,
)

pytestmark = pytest.mark.django_db

MakeConversation = Callable[..., Conversation]
MakeMessage = Callable[..., Message]


def lock_key(conversation_id: int, trigger_message_id: int) -> str:
    return f"lock:{conversation_id}-{trigger_message_id}"


def run_task(conversation: Conversation, trigger: Message) -> None:
    process_conversation(
        conversation_id=conversation.pk,
        trigger_message_id=trigger.pk,
    )


def assistant_messages() -> list[Message]:
    return list(Message.objects.filter(role=MessageRole.ASSISTANT))


class TestDebounce:
    """The customer sends three messages in a row; the AI answers once, at the end."""

    def test_processing_is_scheduled_with_the_configured_window(
        self, settings: Any,
    ) -> None:
        settings.DEBOUNCE_WINDOW_SECONDS = 30

        with mock.patch.object(process_conversation, "apply_async") as apply_async:
            schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

        apply_async.assert_called_once_with(
            kwargs={"conversation_id": 1, "trigger_message_id": 2},
            countdown=30,
        )

    def test_the_window_comes_from_the_settings_and_not_from_a_hardcoded_value(
        self, settings: Any,
    ) -> None:
        settings.DEBOUNCE_WINDOW_SECONDS = 5

        with mock.patch.object(process_conversation, "apply_async") as apply_async:
            schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

        assert apply_async.call_args.kwargs["countdown"] == 5

    def test_a_message_superseded_by_a_newer_one_does_not_call_the_ai(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(seconds=3))

        run_task(conversation, trigger)

        assert runner.agent_was_built is False
        assert runner.ran is False
        assert assistant_messages() == []

    def test_only_the_last_message_of_the_burst_is_answered(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        first = make_message(conversation, NOW, content="quero alugar")
        second = make_message(
            conversation, NOW + timedelta(seconds=2), content="em Boa Viagem",
        )
        last = make_message(conversation, NOW + timedelta(seconds=4), content="até 3000")

        for trigger in (first, second, last):
            run_task(conversation, trigger)

        assert len(assistant_messages()) == 1

    def test_a_newer_assistant_message_does_not_supersede_the_trigger(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Only a message *from the customer* restarts the wait."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(
            conversation, NOW + timedelta(seconds=3), role=MessageRole.ASSISTANT,
        )

        run_task(conversation, trigger)

        assert runner.ran is True

    def test_a_message_from_another_conversation_does_not_supersede_the_trigger(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        other = make_conversation(user_phone=OTHER_PHONE)
        trigger = make_message(conversation, NOW)
        make_message(other, NOW + timedelta(minutes=1))

        run_task(conversation, trigger)

        assert len(assistant_messages()) == 1


class TestIdempotence:
    """The same message must not become two answers — nor two AI bills."""

    def test_a_concurrent_run_of_the_same_trigger_is_discarded(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        cache.set(lock_key(conversation.pk, trigger.pk), "true", 15)

        run_task(conversation, trigger)

        assert runner.agent_was_built is False
        assert assistant_messages() == []

    def test_the_lock_is_released_after_a_successful_run(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_the_lock_is_released_when_the_message_was_superseded(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(minutes=1))

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_the_lock_is_released_even_when_the_ai_fails(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("invalid answer"))

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_the_lock_is_per_trigger_and_not_per_conversation(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        cache.set(lock_key(conversation.pk, trigger.pk + 999), "true", 15)

        run_task(conversation, trigger)

        assert runner.ran is True


class TestInputSentToTheAgent:
    """The run carries only what the model has not seen.

    The conversation itself lives on the provider (``external_conversation_id``),
    so the task sends the customer messages that arrived after the last answer
    and lets the provider supply the rest of the history.
    """

    def test_sends_the_customer_messages_that_are_still_unanswered(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=2), content="oi")
        make_message(
            conversation, NOW - timedelta(minutes=1),
            role=MessageRole.ASSISTANT, content="olá!",
        )
        make_message(conversation, NOW - timedelta(seconds=30), content="quero alugar")
        trigger = make_message(conversation, NOW, content="em Boa Viagem")

        run_task(conversation, trigger)

        assert runner.input_sent == [
            {"role": "user", "content": "quero alugar"},
            {"role": "user", "content": "em Boa Viagem"},
        ]

    def test_does_not_resend_what_the_assistant_already_answered(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Resending an answered message would pay for the same context twice."""

        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=2), content="oi")
        make_message(
            conversation, NOW - timedelta(minutes=1),
            role=MessageRole.ASSISTANT, content="olá!",
        )
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert runner.input_sent == [{"role": "user", "content": "quero alugar"}]

    def test_sends_the_whole_conversation_on_the_first_answer(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=1), content="oi")
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert runner.input_sent == [
            {"role": "user", "content": "oi"},
            {"role": "user", "content": "quero alugar"},
        ]

    def test_attaches_the_run_to_the_provider_conversation(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(external_conversation_id="conv-42")
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        assert runner.external_conversation_sent == "conv-42"

    def test_opens_the_provider_conversation_on_the_first_processing(
        self, runner: FakeRunner, stub_external_conversation: mock.AsyncMock,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Ingestion leaves it empty; the task is where the network call belongs."""

        conversation = make_conversation(external_conversation_id=None)
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        stub_external_conversation.assert_awaited_once()
        assert runner.external_conversation_sent == EXTERNAL_CONVERSATION_ID
        conversation.refresh_from_db()
        assert conversation.external_conversation_id == EXTERNAL_CONVERSATION_ID

    def test_does_not_open_a_second_provider_conversation(
        self, runner: FakeRunner, stub_external_conversation: mock.AsyncMock,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(external_conversation_id=None)
        first = make_message(conversation, NOW, content="quero alugar")
        second = make_message(
            conversation, NOW + timedelta(minutes=1), content="em Boa Viagem",
        )

        run_task(conversation, first)
        run_task(conversation, second)

        stub_external_conversation.assert_awaited_once()

    def test_a_provider_outage_answers_with_the_fallback(
        self, runner: FakeRunner, stub_external_conversation: mock.AsyncMock,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """The customer still gets an answer, and the run never starts."""

        conversation = make_conversation(external_conversation_id=None)
        trigger = make_message(conversation, NOW)
        stub_external_conversation.side_effect = OSError("provider unavailable")

        run_task(conversation, trigger)

        assert runner.ran is False
        assert Message.objects.get(role=MessageRole.ASSISTANT).content == FALLBACK_MESSAGE

    def test_passes_the_conversation_in_the_tool_context(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        assert runner.deps_used.conversation_id == conversation.pk

    def test_does_not_mix_in_the_history_of_another_conversation(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        other = make_conversation(user_phone=OTHER_PHONE)
        make_message(other, NOW - timedelta(minutes=1), content="segredo alheio")
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert runner.input_sent == [{"role": "user", "content": "quero alugar"}]


class TestAgentAnswer:
    def test_stores_the_answer_as_an_assistant_message(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Temos ótimas opções."))

        run_task(conversation, trigger)

        answer = Message.objects.get(role=MessageRole.ASSISTANT)
        assert answer.content == "Temos ótimas opções."
        assert answer.conversation == conversation
        assert answer.timestamp == trigger.timestamp

    def test_records_the_properties_recommended_in_the_answer(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Encontrei estes.", "IMV-001", "IMV-002"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "IMV-002"],
        )

    def test_uses_the_codes_presented_by_the_tool_when_the_answer_lists_none(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """The property was shown to the customer: it must not resurface on the next search."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Veja o que encontrei."), presented=["IMV-099"])

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-099"],
        )

    def test_the_answer_takes_precedence_over_the_presented_codes(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Este aqui.", "IMV-001"), presented=["IMV-099"])

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-001"],
        )

    def test_a_conversation_without_properties_recommends_nothing(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Em qual bairro você procura?"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(conversation_id=conversation.pk, property_codes=[])


class TestFallback:
    """An AI failure must not leave the customer hanging."""

    @pytest.mark.parametrize(
        "error",
        [
            ModelBehaviorError("incomplete answer"),
            MaxTurnsExceeded("exceeded the number of turns"),
            RuntimeError("unexpected error"),
        ],
        ids=["model_behavior", "max_turns", "unexpected"],
    )
    def test_answers_with_the_fallback_message(
        self, runner: FakeRunner, error: Exception,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(error)

        run_task(conversation, trigger)

        assert Message.objects.get(role=MessageRole.ASSISTANT).content == FALLBACK_MESSAGE

    def test_the_task_does_not_propagate_the_error_to_celery(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Propagating would make Celery requeue and bill the AI again."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("failed"))

        run_task(conversation, trigger)  # must not raise

    def test_the_fallback_recommends_no_property_at_all(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("failed"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(conversation_id=conversation.pk, property_codes=[])


class TestExpireInactiveConversations:
    """The beat sweep: the schedule is a setting, the rule lives in the service."""

    def test_closes_a_conversation_idle_past_the_configured_limit(
        self, settings: Any,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        settings.INACTIVITY_TIMEOUT_HOURS = 24
        conversation = make_conversation()
        make_message(conversation, created_at=timezone.now() - timedelta(hours=25))

        assert expire_inactive_conversations() == 1

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED

    def test_the_limit_comes_from_the_settings_and_not_from_a_hardcoded_value(
        self, settings: Any,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        settings.INACTIVITY_TIMEOUT_HOURS = 48
        conversation = make_conversation()
        make_message(conversation, created_at=timezone.now() - timedelta(hours=25))

        assert expire_inactive_conversations() == 0

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE

    def test_a_sweep_with_nothing_to_close_is_a_no_op(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation)

        assert expire_inactive_conversations() == 0
        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 0


class TestServiceLifecycle:
    """One full cycle: served, closed by the beat, reopened by the customer.

    This is what closing is for: the customer who comes back is not answered on
    top of a service that ended days ago — neither on the provider side, which
    gets a brand new conversation, nor on ours, where the closing message is
    the cut point of the history sent to the model.
    """

    def test_a_reopened_conversation_starts_a_new_service(
        self, settings: Any, runner: FakeRunner,
        stub_external_conversation: mock.AsyncMock,
    ) -> None:
        settings.INACTIVITY_TIMEOUT_HOURS = 24
        first = register_message(
            external_id=uuid4(), user_phone=PHONE,
            content="quero alugar", timestamp=timezone.now(),
        )
        run_task(first.conversation, first.message)

        # The customer walks away: the whole service ages past the limit.
        Message.objects.filter(conversation=first.conversation).update(
            created_at=timezone.now() - timedelta(hours=30),
        )

        assert expire_inactive_conversations() == 1

        came_back = register_message(
            external_id=uuid4(), user_phone=PHONE,
            content="voltei", timestamp=timezone.now(),
        )
        run_task(came_back.conversation, came_back.message)

        conversation = Conversation.objects.get(pk=first.conversation.pk)
        assert conversation.status == ConversationStatus.ACTIVE
        # A second call means the old provider conversation was left behind.
        assert stub_external_conversation.await_count == 2
        assert [item["content"] for item in runner.input_sent] == ["voltei"]
