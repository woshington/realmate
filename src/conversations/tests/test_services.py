"""Conversation services.

The layer where ingestion idempotence lives (the same message delivered twice
does not become two conversations) and where the history that feeds the AI is
assembled.
"""

from datetime import timedelta
from typing import Callable
from unittest import mock
from uuid import uuid4

import pytest

from django.utils import timezone

from assistant import CLOSING_MESSAGE
from conversations.enums import ConversationStatus, MessageRole
from conversations.models import Conversation, Message
from conversations.services import (
    add_recommendations,
    close_conversation,
    close_inactive_conversations,
    ensure_external_conversation,
    get_recent_messages,
    has_newer_customer_message,
    register_message,
    touch_last_message_at,
)
from properties.models import Property

from .conftest import EXTERNAL_CONVERSATION_ID, NOW, OTHER_PHONE, PHONE

pytestmark = pytest.mark.django_db

MakeConversation = Callable[..., Conversation]
MakeMessage = Callable[..., Message]
MakeProperty = Callable[..., Property]


class TestRegisterMessageIdempotence:
    """The messaging provider redelivers; the database must not duplicate."""

    def test_creates_conversation_and_message_on_the_first_call(self) -> None:
        ingestion = register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        assert ingestion.created is True
        assert ingestion.conversation.user_phone == PHONE
        assert ingestion.message.role == MessageRole.CUSTOMER
        assert ingestion.conversation.last_message_at == NOW

    def test_the_same_external_id_does_not_duplicate_the_message(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        ingestion = register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="conteúdo diferente",
            timestamp=NOW + timedelta(minutes=5),
        )

        assert ingestion.created is False
        assert Message.objects.count() == 1

    def test_a_redelivery_does_not_overwrite_the_original_content(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="adulterado",
            timestamp=NOW,
        )

        assert Message.objects.get().content == "Olá"

    def test_a_redelivery_does_not_move_the_conversation_timeline(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="Olá",
            timestamp=NOW + timedelta(minutes=5),
        )

        assert Conversation.objects.get().last_message_at == NOW


class TestRegisterMessageConversation:
    def test_the_same_phone_reuses_the_conversation(self) -> None:
        register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )
        register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="tudo bem?",
            timestamp=NOW + timedelta(minutes=1),
        )

        assert Conversation.objects.count() == 1
        assert Message.objects.count() == 2

    def test_different_phones_produce_different_conversations(self) -> None:
        register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )
        register_message(
            external_id=uuid4(), user_phone=OTHER_PHONE, content="Oi", timestamp=NOW,
        )

        assert Conversation.objects.count() == 2

    def test_ingestion_does_not_open_the_conversation_on_the_provider(
        self, stub_external_conversation: mock.AsyncMock,
    ) -> None:
        """The webhook must not depend on the provider being up to accept a message."""

        register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        assert Conversation.objects.get().external_conversation_id is None
        stub_external_conversation.assert_not_called()

    def test_the_default_role_is_customer_but_can_be_overridden(self) -> None:
        ingestion = register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="Resposta automática",
            timestamp=NOW,
            role=MessageRole.ASSISTANT,
        )

        assert ingestion.message.role == MessageRole.ASSISTANT


class TestReopenOnANewMessage:
    """The customer who comes back starts a new service, not the old one.

    Reopening throws away the provider conversation: the next processing opens a
    fresh one, so the agent does not start the new service already carrying the
    history of the one that was closed.
    """

    def test_a_customer_message_reopens_a_closed_conversation(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(status=ConversationStatus.CLOSED)

        register_message(
            external_id=uuid4(), user_phone=PHONE, content="voltei", timestamp=NOW,
        )

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE

    def test_reopening_drops_the_provider_conversation(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(status=ConversationStatus.CLOSED)

        register_message(
            external_id=uuid4(), user_phone=PHONE, content="voltei", timestamp=NOW,
        )

        conversation.refresh_from_db()
        assert conversation.external_conversation_id is None

    def test_an_answer_from_the_assistant_does_not_reopen(
        self, make_conversation: MakeConversation,
    ) -> None:
        """Otherwise the closing message itself would undo the closing."""

        conversation = make_conversation(status=ConversationStatus.CLOSED)

        register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content=CLOSING_MESSAGE,
            timestamp=NOW,
            role=MessageRole.ASSISTANT,
        )

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED

    def test_a_redelivery_does_not_reopen(
        self, make_conversation: MakeConversation,
    ) -> None:
        """The provider redelivering an old message is not the customer coming back."""

        external_id = uuid4()
        conversation = make_conversation()
        register_message(
            external_id=external_id, user_phone=PHONE, content="oi", timestamp=NOW,
        )
        close_conversation(conversation)

        register_message(
            external_id=external_id, user_phone=PHONE, content="oi", timestamp=NOW,
        )

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED

    def test_an_active_conversation_keeps_its_provider_conversation(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation()

        register_message(
            external_id=uuid4(), user_phone=PHONE, content="oi", timestamp=NOW,
        )

        conversation.refresh_from_db()
        assert conversation.external_conversation_id == EXTERNAL_CONVERSATION_ID


class TestCloseConversation:
    def test_closes_the_conversation_and_warns_the_customer(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation()

        assert close_conversation(conversation) is True

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED
        assert conversation.messages.count() == 1
        closing = conversation.messages.get()
        assert closing.role == MessageRole.ASSISTANT
        assert closing.content == CLOSING_MESSAGE

    def test_closing_twice_does_not_warn_twice(
        self, make_conversation: MakeConversation,
    ) -> None:
        """Two concurrent beat runs must not send two closing messages."""

        conversation = make_conversation()
        close_conversation(conversation)

        assert close_conversation(conversation) is False
        assert conversation.messages.count() == 1

    def test_closing_does_not_move_the_conversation_timeline(
        self, make_conversation: MakeConversation,
    ) -> None:
        """The closing message is the system talking, not activity in the service."""

        conversation = make_conversation(last_message_at=NOW)

        close_conversation(conversation)

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_the_closing_message_becomes_the_cut_point_of_the_next_service(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """What makes the reopened service start clean on this side.

        ``get_recent_messages`` cuts at the last answer from the assistant, and
        the closing message is one — so the agent is only told what came in
        after the conversation was reopened.
        """

        now = timezone.now()
        conversation = make_conversation()
        make_message(conversation, now - timedelta(minutes=2), content="quero alugar")
        make_message(
            conversation, now - timedelta(minutes=1),
            role=MessageRole.ASSISTANT, content="Em qual bairro?",
        )
        close_conversation(conversation)
        came_back = make_message(
            conversation, now + timedelta(days=2), content="voltei",
        )

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [came_back.pk]


class TestCloseInactiveConversations:
    """The customer who disappears does not leave the service open forever.

    Inactivity is counted from when the message was *stored*, not from the
    timestamp the provider sent: the two drift apart, and the provider's is not
    ours to trust.
    """

    def test_closes_a_conversation_past_the_limit(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, created_at=timezone.now() - timedelta(hours=25))

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 1

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED

    def test_keeps_a_conversation_still_inside_the_window(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, created_at=timezone.now() - timedelta(hours=23))

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 0

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE

    def test_a_provider_that_repeats_the_timestamp_does_not_end_a_live_service(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """The regression: every message arriving with the same timestamp.

        ``last_message_at`` then stays frozen in the past while the customer is
        still typing — and counting inactivity from it closes the service in the
        middle of the conversation.
        """

        frozen = NOW - timedelta(days=30)
        conversation = make_conversation(last_message_at=frozen)
        make_message(conversation, timestamp=frozen, created_at=timezone.now())

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 0

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE

    def test_a_reopened_conversation_is_not_closed_again_right_away(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Otherwise the service flaps and the customer gets closing messages in a row."""

        conversation = make_conversation(last_message_at=NOW)
        make_message(conversation, created_at=timezone.now() - timedelta(hours=25))
        close_conversation(conversation)

        register_message(
            external_id=uuid4(), user_phone=PHONE, content="voltei", timestamp=NOW,
        )

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 0

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE
        assert conversation.messages.filter(content=CLOSING_MESSAGE).count() == 1

    def test_a_conversation_that_never_got_a_message_ages_by_its_creation_date(
        self, make_conversation: MakeConversation,
    ) -> None:
        """Without this it would stay active forever: there is nothing to aggregate."""

        conversation = make_conversation()
        Conversation.objects.filter(pk=conversation.pk).update(
            created_at=timezone.now() - timedelta(hours=25),
        )

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 1

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.CLOSED

    def test_a_recent_conversation_without_messages_is_kept(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation()

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 0

        conversation.refresh_from_db()
        assert conversation.status == ConversationStatus.ACTIVE

    def test_an_already_closed_conversation_is_not_closed_again(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(status=ConversationStatus.CLOSED)
        make_message(conversation, created_at=timezone.now() - timedelta(hours=25))

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 0
        assert conversation.messages.filter(content=CLOSING_MESSAGE).count() == 0

    def test_closes_only_what_is_past_the_limit(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        stale = make_conversation(user_phone=PHONE)
        make_message(stale, created_at=timezone.now() - timedelta(hours=30))
        active = make_conversation(user_phone=OTHER_PHONE)
        make_message(active, created_at=timezone.now() - timedelta(hours=1))

        assert close_inactive_conversations(idle_for=timedelta(hours=24)) == 1

        stale.refresh_from_db()
        active.refresh_from_db()
        assert stale.status == ConversationStatus.CLOSED
        assert active.status == ConversationStatus.ACTIVE


class TestEnsureExternalConversation:
    """The provider conversation is opened lazily, on the first processing."""

    def test_opens_the_conversation_on_the_provider_when_there_is_none(
        self, make_conversation: MakeConversation,
        stub_external_conversation: mock.AsyncMock,
    ) -> None:
        conversation = make_conversation(external_conversation_id=None)

        result = ensure_external_conversation(conversation)

        assert result == EXTERNAL_CONVERSATION_ID
        stub_external_conversation.assert_awaited_once()

    def test_stores_the_id_so_the_next_run_reuses_it(
        self, make_conversation: MakeConversation,
        stub_external_conversation: mock.AsyncMock,
    ) -> None:
        conversation = make_conversation(external_conversation_id=None)

        ensure_external_conversation(conversation)

        conversation.refresh_from_db()
        assert conversation.external_conversation_id == EXTERNAL_CONVERSATION_ID

    def test_does_not_call_the_provider_again_once_the_id_exists(
        self, make_conversation: MakeConversation,
        stub_external_conversation: mock.AsyncMock,
    ) -> None:
        conversation = make_conversation(external_conversation_id="conv-42")

        assert ensure_external_conversation(conversation) == "conv-42"
        stub_external_conversation.assert_not_called()

    def test_a_concurrent_run_that_won_the_race_keeps_its_id(
        self, make_conversation: MakeConversation,
        stub_external_conversation: mock.AsyncMock,
    ) -> None:
        """The write only lands on a conversation that still has no id."""

        conversation = make_conversation(external_conversation_id=None)
        Conversation.objects.filter(pk=conversation.pk).update(
            external_conversation_id="conv-from-the-other-worker",
        )

        result = ensure_external_conversation(conversation)

        assert result == "conv-from-the-other-worker"
        assert Conversation.objects.get().external_conversation_id == (
            "conv-from-the-other-worker"
        )


class TestTouchLastMessageAt:
    """``last_message_at`` only moves forward."""

    def test_sets_it_when_there_is_none_yet(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=None)

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_moves_forward_with_a_more_recent_timestamp(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW + timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW + timedelta(minutes=10)

    def test_does_not_go_back_with_an_out_of_order_message(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW - timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_does_not_write_when_the_timestamp_equals_the_current_one(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)
        updated_at_before = conversation.updated_at

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.updated_at == updated_at_before


class TestHasNewerCustomerMessage:
    """The question that decides whether the debounce still holds."""

    def test_true_when_the_customer_sent_another_message(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        reference = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(minutes=1))

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        ) is True

    def test_false_when_there_is_no_newer_message(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        reference = make_message(conversation, NOW)

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        ) is False

    def test_false_when_the_newest_one_is_from_the_assistant(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        reference = make_message(conversation, NOW)
        make_message(
            conversation, NOW + timedelta(minutes=1), role=MessageRole.ASSISTANT,
        )

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        ) is False

    def test_ignores_a_newer_message_from_another_conversation(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        other = make_conversation(user_phone=OTHER_PHONE)
        reference = make_message(conversation, NOW)
        make_message(other, NOW + timedelta(minutes=1))

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        ) is False


class TestGetRecentMessages:
    """What the model still has to be told.

    The conversation history lives on the provider side (see
    ``Conversation.external_conversation_id``), so the task only sends what the
    assistant has not answered yet: the customer messages that came in after the
    last answer.
    """

    def test_returns_the_whole_conversation_while_the_assistant_has_not_answered(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        first = make_message(conversation, NOW - timedelta(minutes=2), content="1")
        middle = make_message(conversation, NOW - timedelta(minutes=1), content="2")
        last = make_message(conversation, NOW, content="3")

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [first.pk, middle.pk, last.pk]

    def test_returns_only_the_customer_messages_since_the_last_answer(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=3), content="quero alugar")
        make_message(
            conversation, NOW - timedelta(minutes=2),
            role=MessageRole.ASSISTANT, content="Em qual bairro?",
        )
        pending = make_message(
            conversation, NOW - timedelta(minutes=1), content="Boa Viagem",
        )
        newest = make_message(conversation, NOW, content="até 3000")

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [pending.pk, newest.pk]

    def test_the_cut_point_is_the_most_recent_answer(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(
            conversation, NOW - timedelta(minutes=3),
            role=MessageRole.ASSISTANT, content="Olá!",
        )
        make_message(conversation, NOW - timedelta(minutes=2), content="quero alugar")
        make_message(
            conversation, NOW - timedelta(minutes=1),
            role=MessageRole.ASSISTANT, content="Em qual bairro?",
        )
        pending = make_message(conversation, NOW, content="Boa Viagem")

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [pending.pk]

    def test_an_answered_conversation_without_new_messages_returns_an_empty_list(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Nothing pending means there is nothing to send the model."""

        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=1), content="quero alugar")
        make_message(
            conversation, NOW, role=MessageRole.ASSISTANT, content="Em qual bairro?",
        )

        assert get_recent_messages(conversation_id=conversation.pk) == []

    def test_does_not_mix_in_messages_from_another_conversation(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        other = make_conversation(user_phone=OTHER_PHONE)
        make_message(other, NOW)
        own = make_message(conversation, NOW)

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [own.pk]

    def test_an_answer_in_another_conversation_does_not_move_the_cut_point(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        other = make_conversation(user_phone=OTHER_PHONE)
        own = make_message(conversation, NOW - timedelta(minutes=1), content="oi")
        make_message(other, NOW, role=MessageRole.ASSISTANT, content="Olá!")

        result = get_recent_messages(conversation_id=conversation.pk)

        assert [message.pk for message in result] == [own.pk]

    def test_a_conversation_without_messages_returns_an_empty_list(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation()

        assert get_recent_messages(conversation_id=conversation.pk) == []


class TestAddRecommendations:
    def test_links_the_properties_by_code(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        first = make_property(code="IMV-001")
        second = make_property(code="IMV-002")

        add_recommendations(
            conversation_id=conversation.pk, property_codes=["IMV-001", "IMV-002"],
        )

        assert set(conversation.recommended_properties.all()) == {first, second}

    def test_ignores_a_code_that_does_not_exist(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        existing = make_property(code="IMV-001")

        add_recommendations(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "CODIGO-INEXISTENTE"],
        )

        assert list(conversation.recommended_properties.all()) == [existing]

    def test_an_empty_list_links_nothing(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        make_property(code="IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=[])

        assert conversation.recommended_properties.count() == 0

    def test_repeated_calls_do_not_duplicate_the_link(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        make_property(code="IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])
        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])

        assert conversation.recommended_properties.count() == 1
