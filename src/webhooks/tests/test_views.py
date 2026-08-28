import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from conversations.enums import MessageRole
from conversations.models import Conversation, Message

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def url() -> str:
    return reverse("webhooks:message")


@pytest.fixture(autouse=True)
def scheduler() -> Any:
    """Isola o agendamento: os testes do webhook não dependem de um broker."""

    with patch("webhooks.views.schedule_conversation_processing") as mock:
        yield mock


def message_received(**overrides: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "message_id": str(uuid4()),
        "user_phone_number": PHONE,
        "message_content": "Olá, procuro apartamento para alugar em Boa Viagem",
        "timestamp": "2026-06-02T10:00:00Z",
    }
    content.update(overrides)
    return {"event": "MESSAGE_RECEIVED", "content": content}


def post(client: Client, url: str, payload: dict[str, Any]) -> Any:
    return client.post(url, data=payload, content_type="application/json")


def test_mensagem_recebida_e_persistida_e_aceita(
    client: Client, url: str, scheduler: Any
) -> None:
    payload = message_received()

    response = post(client, url, payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "message_id": payload["content"]["message_id"],
    }

    message = Message.objects.get()
    assert message.role == MessageRole.CUSTOMER
    assert message.content == payload["content"]["message_content"]
    assert message.timestamp == NOW
    assert message.conversation.user_phone == PHONE
    scheduler.assert_called_once_with(
        conversation_id=message.conversation.pk, trigger_message_id=message.pk
    )


def test_evento_desconhecido_e_ignorado_sem_persistir(
    client: Client, url: str, scheduler: Any
) -> None:
    message_id = str(uuid4())
    payload = {
        "event": "MESSAGE_READ",
        "content": {
            "message_id": message_id,
            "user_phone_number": PHONE,
            "read_at": "2026-06-02T10:00:10Z",
        },
    }

    response = post(client, url, payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": message_id}
    assert Message.objects.count() == 0
    assert Conversation.objects.count() == 0
    scheduler.assert_not_called()


def test_message_id_repetido_e_ignorado_silenciosamente(
    client: Client, url: str, scheduler: Any
) -> None:
    payload = message_received()
    post(client, url, payload)
    scheduler.reset_mock()

    response = post(client, url, payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "message_id": payload["content"]["message_id"],
    }
    assert Message.objects.count() == 1
    # Reentrega não pode adiar a resposta da IA reagendando o debounce.
    scheduler.assert_not_called()


def test_mensagens_do_mesmo_telefone_reusam_a_conversa(
    client: Client, url: str
) -> None:
    post(client, url, message_received())
    post(
        client,
        url,
        message_received(
            message_content="até R$ 3.000", timestamp="2026-06-02T10:00:04Z"
        ),
    )

    assert Conversation.objects.count() == 1
    conversation = Conversation.objects.get()
    assert conversation.messages.count() == 2
    assert conversation.last_message_at == NOW + timedelta(seconds=4)


@pytest.mark.parametrize(
    ("overrides", "campo"),
    [
        ({"user_phone_number": "81982860171"}, "user_phone_number"),
        ({"message_id": "nao-e-um-uuid"}, "message_id"),
        ({"message_content": "   "}, "message_content"),
        ({"timestamp": "ontem"}, "timestamp"),
    ],
)
def test_payload_invalido_retorna_400(
    client: Client, url: str, overrides: dict[str, Any], campo: str
) -> None:
    response = post(client, url, message_received(**overrides))

    assert response.status_code == 400
    assert campo in response.json()
    assert Message.objects.count() == 0


def test_campo_obrigatorio_ausente_retorna_400(client: Client, url: str) -> None:
    payload = message_received()
    del payload["content"]["user_phone_number"]

    response = post(client, url, payload)

    assert response.status_code == 400
    assert "user_phone_number" in response.json()
    assert Message.objects.count() == 0


def test_corpo_nao_json_retorna_400(client: Client, url: str) -> None:
    response = client.post(url, data="isso nao e json", content_type="application/json")

    assert response.status_code == 400


def test_envelope_sem_evento_retorna_400(client: Client, url: str) -> None:
    response = post(client, url, {"content": {}})

    assert response.status_code == 400
    assert "event" in response.json()


def test_content_type_nao_json_retorna_415(client: Client, url: str) -> None:
    response = client.post(url, data={"event": "MESSAGE_RECEIVED"})

    assert response.status_code == 415
    assert Message.objects.count() == 0


def test_metodo_nao_permitido(client: Client, url: str) -> None:
    assert client.get(url).status_code == 405


def test_webhook_nao_exige_csrf() -> None:
    enforcing_client = Client(enforce_csrf_checks=True)

    response = enforcing_client.post(
        reverse("webhooks:message"),
        data=json.dumps(message_received()),
        content_type="application/json",
    )

    assert response.status_code == 200
