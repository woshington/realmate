"""Shared test-suite configuration.

Two things live here:

1. The infrastructure isolation — cache and tracing — that no test should have
   to set up on its own.
2. The domain object factories (``make_conversation``, ``make_message``,
   ``make_property``) used by more than one app. Keeping them here stops every
   test package from reinventing the same ``objects.create``.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator
from unittest import mock

import pytest
from agents import set_tracing_disabled
from django.core.cache import cache

from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from properties.enums import TransactionType
from properties.models import Property

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
EXTERNAL_CONVERSATION_ID = "conv-test"


@pytest.fixture(autouse=True, scope="session")
def disable_agent_tracing() -> Iterator[None]:
    """No test is allowed to talk to OpenAI.

    The SDK starts a background trace exporter that POSTs to
    ``/v1/traces/ingest`` on every agent run. Disabled here, the suite runs
    offline and does not depend on a valid credential.
    """

    set_tracing_disabled(True)
    yield


@pytest.fixture(autouse=True)
def local_cache(settings: Any) -> None:
    """In-memory cache, cleared on every test.

    In production the cache is Redis (see ``config.settings.CACHES``) and holds
    the conversation processing lock. A unit test must not depend on a running
    service nor pollute the developer's cache database.

    Replacing ``settings.CACHES`` fires the ``setting_changed`` signal, which
    makes Django rebuild the handler — so the swap applies even to code that
    already imported ``django.core.cache.cache``.
    """

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "realmate-tests",
        }
    }
    cache.clear()


@pytest.fixture(autouse=True)
def stub_external_conversation() -> Iterator[mock.AsyncMock]:
    """Opening a conversation on the provider is a network call.

    ``ensure_external_conversation`` asks the provider for a conversation id the
    first time a conversation is processed. In the suite that id is handed out
    locally, so the tests stay offline and every conversation ends up with a
    predictable ``external_conversation_id``.
    """

    with mock.patch(
        "conversations.services.create_conversation",
        new=mock.AsyncMock(return_value=EXTERNAL_CONVERSATION_ID),
    ) as create:
        yield create


@pytest.fixture
def make_conversation() -> Callable[..., Conversation]:
    def _make(
        user_phone: str = PHONE,
        last_message_at: datetime | None = None,
        external_conversation_id: str = EXTERNAL_CONVERSATION_ID,
    ) -> Conversation:
        return Conversation.objects.create(
            user_phone=user_phone,
            last_message_at=last_message_at,
            external_conversation_id=external_conversation_id,
        )

    return _make


@pytest.fixture
def make_message() -> Callable[..., Message]:
    def _make(
        conversation: Conversation,
        timestamp: datetime = NOW,
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

    return _make


@pytest.fixture
def make_property() -> Callable[..., Property]:
    def _make(
        code: str = "IMV-001",
        transaction_type: str = TransactionType.RENT,
        neighborhood: str = "Boa Viagem",
        price: str = "2500.00",
        bedrooms: int = 2,
        address: str = "Rua dos Navegantes, 150",
        description: str = "Apartamento com varanda",
    ) -> Property:
        return Property.objects.create(
            code=code,
            transaction_type=transaction_type,
            neighborhood=neighborhood,
            price=Decimal(price),
            bedrooms=bedrooms,
            address=address,
            description=description,
        )

    return _make
