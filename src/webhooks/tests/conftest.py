"""Ferramental dos testes de webhook."""

from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
URL = reverse("webhooks:message")


@pytest.fixture
def scheduler() -> Iterator[MagicMock]:
    """Impede que o teste enfileire tarefa de verdade no Celery."""

    with patch("webhooks.views.schedule_conversation_processing") as mocked:
        yield mocked


@pytest.fixture
def message_event() -> Callable[..., dict[str, Any]]:
    """Payload de ``MESSAGE_RECEIVED`` no formato que o provedor entrega."""

    def _event(
        message_id: str | None = None,
        phone: str = PHONE,
        content: str = "Olá, procuro apartamento",
        timestamp: datetime = NOW,
    ) -> dict[str, Any]:
        return {
            "event": "MESSAGE_RECEIVED",
            "content": {
                "message_id": message_id or str(uuid4()),
                "user_phone_number": phone,
                "message_content": content,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            },
        }

    return _event


@pytest.fixture
def post(client: Client) -> Callable[[dict[str, Any]], Any]:
    def _post(payload: dict[str, Any]) -> Any:
        return client.post(URL, data=payload, content_type="application/json")

    return _post
