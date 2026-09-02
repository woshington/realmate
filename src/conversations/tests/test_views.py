"""``GET /api/conversations/{user_phone}/messages``.

The read contract for the history: this is where the challenge reviewer checks
what the assistant answered and which properties were presented.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from django.test import Client
from django.urls import reverse

from conversations.enums import MessageRole
from conversations.models import Conversation, Message, PropertyRecommendation
from properties.models import Property

from .conftest import NOW, OTHER_PHONE, PHONE

pytestmark = pytest.mark.django_db

MakeConversation = Callable[..., Conversation]
MakeMessage = Callable[..., Message]
MakeProperty = Callable[..., Property]


def url_for(user_phone: str) -> str:
    return reverse("conversations:messages", kwargs={"user_phone": user_phone})


@pytest.fixture
def conversation(
    make_conversation: MakeConversation, make_message: MakeMessage,
) -> Conversation:
    conversation = make_conversation()
    make_message(
        conversation,
        NOW,
        role=MessageRole.CUSTOMER,
        content="Olá, procuro apartamento para alugar em Boa Viagem",
    )
    make_message(
        conversation,
        NOW + timedelta(seconds=5),
        role=MessageRole.ASSISTANT,
        content="Olá! Encontrei 2 imóveis que podem atender você...",
    )
    return conversation


class TestResponseFormat:
    def test_returns_the_history_in_the_contract_format(
        self, client: Client, conversation: Conversation,
    ) -> None:
        response = client.get(url_for(PHONE))

        assert response.status_code == 200
        assert response.json() == {
            "user_phone": PHONE,
            "properties_found": [],
            "messages": [
                {
                    "role": "customer",
                    "content": "Olá, procuro apartamento para alugar em Boa Viagem",
                    "timestamp": "2026-06-02T10:00:00Z",
                },
                {
                    "role": "assistant",
                    "content": "Olá! Encontrei 2 imóveis que podem atender você...",
                    "timestamp": "2026-06-02T10:00:05Z",
                },
            ],
        }

    def test_a_conversation_without_messages_returns_empty_lists(
        self, client: Client, make_conversation: MakeConversation,
    ) -> None:
        make_conversation()

        assert client.get(url_for(PHONE)).json() == {
            "user_phone": PHONE,
            "properties_found": [],
            "messages": [],
        }

    def test_the_timestamp_is_returned_in_utc_with_the_z_suffix(
        self, client: Client, conversation: Conversation, make_message: MakeMessage,
    ) -> None:
        make_message(
            conversation,
            datetime(2026, 6, 2, 8, 0, tzinfo=timezone(timedelta(hours=-3))),
            content="enviada em -03:00",
        )

        timestamps = [
            message["timestamp"]
            for message in client.get(url_for(PHONE)).json()["messages"]
        ]

        assert "2026-06-02T11:00:00Z" in timestamps


class TestOrdering:
    def test_messages_come_from_the_oldest_to_the_newest(
        self, client: Client, conversation: Conversation, make_message: MakeMessage,
    ) -> None:
        make_message(
            conversation,
            NOW - timedelta(minutes=1),
            content="mais antiga, inserida por último",
        )

        timestamps = [
            message["timestamp"]
            for message in client.get(url_for(PHONE)).json()["messages"]
        ]

        assert timestamps == sorted(timestamps)
        assert timestamps[0] == "2026-06-02T09:59:00Z"

    def test_properties_found_follows_the_recommendation_order(
        self, client: Client, conversation: Conversation, make_property: MakeProperty,
    ) -> None:
        for code in ("IMV-001", "C011"):
            PropertyRecommendation.objects.create(
                conversation=conversation, property=make_property(code=code),
            )

        found = client.get(url_for(PHONE)).json()["properties_found"]

        assert found == ["IMV-001", "C011"]


class TestIsolationBetweenConversations:
    def test_a_recommendation_from_another_conversation_does_not_leak(
        self, client: Client, conversation: Conversation,
        make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        other = make_conversation(user_phone=OTHER_PHONE)
        PropertyRecommendation.objects.create(
            conversation=other, property=make_property(code="IMV-999"),
        )

        assert client.get(url_for(PHONE)).json()["properties_found"] == []

    def test_a_message_from_another_conversation_does_not_leak(
        self, client: Client, conversation: Conversation,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        other = make_conversation(user_phone=OTHER_PHONE)
        make_message(other, NOW, content="segredo alheio")

        contents = [
            message["content"]
            for message in client.get(url_for(PHONE)).json()["messages"]
        ]

        assert "segredo alheio" not in contents


class TestPhoneResolution:
    def test_a_phone_without_the_plus_resolves_to_the_same_conversation(
        self, client: Client, conversation: Conversation,
    ) -> None:
        """Some clients drop the ``+`` when building the URL."""

        response = client.get(url_for(PHONE.removeprefix("+")))

        assert response.status_code == 200
        assert response.json()["user_phone"] == PHONE

    def test_an_unknown_phone_returns_404(self, client: Client) -> None:
        assert client.get(url_for("+5581900000000")).status_code == 404

    def test_a_malformed_phone_does_not_match_the_route(self, client: Client) -> None:
        assert client.get("/api/conversations/abc/messages").status_code == 404


class TestAllowedMethods:
    def test_the_route_is_read_only(
        self, client: Client, conversation: Conversation,
    ) -> None:
        assert client.post(url_for(PHONE)).status_code == 405
