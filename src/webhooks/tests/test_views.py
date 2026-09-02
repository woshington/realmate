"""Messaging webhook contract.

The webhook is the system's only message entry point and talks to an external
provider that redelivers. The guarantees tested here — idempotence, event
filtering and response format — are what keeps a redelivery from becoming a
second AI bill.
"""

from datetime import timedelta
from typing import Any, Callable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from django.test import Client

from conversations.enums import MessageRole
from conversations.models import Conversation, Message

from .conftest import NOW, OTHER_PHONE, PHONE, URL

pytestmark = pytest.mark.django_db

Post = Callable[[dict[str, Any]], Any]
MessageEvent = Callable[..., dict[str, Any]]


class TestMessageReceived:
    def test_answers_in_the_contract_format(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        payload = message_event()

        response = post(payload)

        assert response.status_code == 200
        assert response.json() == {
            "status": "accepted",
            "message_id": payload["content"]["message_id"],
        }

    def test_persists_the_conversation_and_the_message(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        payload = message_event(content="Quero alugar em Boa Viagem")

        post(payload)

        conversation = Conversation.objects.get()
        message = Message.objects.get()
        assert conversation.user_phone == PHONE
        assert message.conversation == conversation
        assert message.role == MessageRole.CUSTOMER
        assert message.content == "Quero alugar em Boa Viagem"
        assert str(message.external_id) == payload["content"]["message_id"]
        assert message.timestamp == NOW

    def test_schedules_the_conversation_processing(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event())

        scheduler.assert_called_once_with(
            conversation_id=Conversation.objects.get().pk,
            trigger_message_id=Message.objects.get().pk,
        )

    def test_the_ai_is_not_called_inside_the_request(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        """The view has to return 200 before the provider times out."""

        post(message_event())

        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 0

    def test_a_second_message_from_the_same_phone_reuses_the_conversation(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event(content="Oi"))
        post(message_event(content="bom dia", timestamp=NOW + timedelta(seconds=3)))

        assert Conversation.objects.count() == 1
        assert Message.objects.count() == 2
        assert scheduler.call_count == 2

    def test_different_phones_produce_different_conversations(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event(phone=PHONE))
        post(message_event(phone=OTHER_PHONE))

        assert Conversation.objects.count() == 2


class TestIdempotence:
    """A provider redelivery must not cost a second answer."""

    def test_the_same_message_id_is_ignored(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))

        response = post(message_event(message_id=message_id))

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": message_id}
        assert Message.objects.count() == 1

    def test_a_redelivery_does_not_schedule_a_second_processing(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))
        post(message_event(message_id=message_id))

        scheduler.assert_called_once()

    def test_a_redelivery_with_different_content_does_not_overwrite_the_original(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id, content="original"))

        post(message_event(message_id=message_id, content="adulterado"))

        assert Message.objects.get().content == "original"

    def test_a_redelivery_does_not_create_a_second_conversation(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))
        post(message_event(message_id=message_id))

        assert Conversation.objects.count() == 1


class TestIgnoredEvents:
    """An event we do not handle returns 200 and has no side effect."""

    def test_message_read_is_ignored_without_processing(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())

        response = post(
            {
                "event": "MESSAGE_READ",
                "content": {
                    "message_id": message_id,
                    "user_phone_number": PHONE,
                    "read_at": "2026-06-02T10:00:10Z",
                },
            }
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": message_id}
        assert Message.objects.count() == 0
        assert Conversation.objects.count() == 0
        scheduler.assert_not_called()

    def test_an_unknown_event_answers_200_so_it_is_not_redelivered(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        """A 4xx would make the provider resend an event we do not handle forever."""

        response = post({"event": "EVENTO_DO_FUTURO", "content": {}})

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": None}

    def test_an_ignored_event_without_content_does_not_break(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"event": "MESSAGE_READ"})

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_an_ignored_event_with_a_non_textual_message_id_does_not_break(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"event": "MESSAGE_READ", "content": {"message_id": 42}})

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": None}


class TestInvalidPayload:
    """A malformed payload is the caller's error: 400 and nothing persisted."""

    def test_an_envelope_without_event_is_rejected(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"content": {}})

        assert response.status_code == 400
        scheduler.assert_not_called()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("message_id", "nao-e-uuid"),
            ("user_phone_number", "81982860171"),
            ("user_phone_number", "+55819"),
            ("message_content", ""),
            ("message_content", "   "),
            ("timestamp", "ontem"),
        ],
    )
    def test_invalid_content_is_rejected_without_persisting(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
        field: str, value: str,
    ) -> None:
        payload = message_event()
        payload["content"][field] = value

        response = post(payload)

        assert response.status_code == 400
        assert Message.objects.count() == 0
        scheduler.assert_not_called()

    def test_a_missing_required_field_is_rejected(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        payload = message_event()
        del payload["content"]["message_content"]

        response = post(payload)

        assert response.status_code == 400
        assert Message.objects.count() == 0


class TestAllowedMethods:
    def test_get_is_not_allowed(self, client: Client) -> None:
        assert client.get(URL).status_code == 405
