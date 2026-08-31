"""Contrato do webhook de mensageria.

O que este arquivo protege: o webhook é a única porta de entrada de mensagem do
sistema e conversa com um provedor externo que reentrega. As três garantias
testadas aqui — idempotência, filtro de evento e formato da resposta — são o que
impede uma reentrega de virar uma segunda cobrança de IA para o cliente.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from conversations.enums import MessageRole
from conversations.models import Conversation, Message

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
URL = reverse("webhooks:message")


@pytest.fixture
def scheduler() -> Iterator[MagicMock]:
    """Impede que o teste enfileire tarefa de verdade no Celery."""

    with patch("webhooks.views.schedule_conversation_processing") as mock:
        yield mock


def message_event(
    *,
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


def post(client: Client, payload: dict[str, Any]) -> Any:
    return client.post(URL, data=payload, content_type="application/json")


# --- Evento de mensagem -----------------------------------------------------


def test_mensagem_valida_e_aceita_no_formato_do_contrato(
    client: Client, scheduler: MagicMock
) -> None:
    payload = message_event()

    response = post(client, payload)

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "message_id": payload["content"]["message_id"],
    }


def test_mensagem_valida_persiste_conversa_e_mensagem(
    client: Client, scheduler: MagicMock
) -> None:
    payload = message_event(content="Quero alugar em Boa Viagem")

    post(client, payload)

    conversation = Conversation.objects.get()
    message = Message.objects.get()
    assert conversation.user_phone == PHONE
    assert message.conversation == conversation
    assert message.role == MessageRole.CUSTOMER
    assert message.content == "Quero alugar em Boa Viagem"
    assert str(message.external_id) == payload["content"]["message_id"]
    assert message.timestamp == NOW


def test_mensagem_valida_agenda_o_processamento_da_conversa(
    client: Client, scheduler: MagicMock
) -> None:
    post(client, message_event())

    conversation = Conversation.objects.get()
    message = Message.objects.get()
    scheduler.assert_called_once_with(
        conversation_id=conversation.pk, trigger_message_id=message.pk
    )


def test_segunda_mensagem_do_mesmo_telefone_reusa_a_conversa(
    client: Client, scheduler: MagicMock
) -> None:
    post(client, message_event(content="Oi"))
    post(client, message_event(content="bom dia", timestamp=NOW + timedelta(seconds=3)))

    assert Conversation.objects.count() == 1
    assert Message.objects.count() == 2
    assert scheduler.call_count == 2


def test_telefones_diferentes_geram_conversas_diferentes(
    client: Client, scheduler: MagicMock
) -> None:
    post(client, message_event(phone=PHONE))
    post(client, message_event(phone="+5581999998888"))

    assert Conversation.objects.count() == 2


# --- Idempotência -----------------------------------------------------------


def test_reentrega_do_mesmo_message_id_e_ignorada(
    client: Client, scheduler: MagicMock
) -> None:
    message_id = str(uuid4())
    post(client, message_event(message_id=message_id))

    response = post(client, message_event(message_id=message_id))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": message_id}
    assert Message.objects.count() == 1


def test_reentrega_nao_agenda_um_segundo_processamento(
    client: Client, scheduler: MagicMock
) -> None:
    message_id = str(uuid4())
    post(client, message_event(message_id=message_id))
    post(client, message_event(message_id=message_id))

    # A garantia que mais importa: reentrega não custa uma segunda chamada de IA.
    scheduler.assert_called_once()


def test_reentrega_com_conteudo_diferente_nao_sobrescreve_o_original(
    client: Client, scheduler: MagicMock
) -> None:
    message_id = str(uuid4())
    post(client, message_event(message_id=message_id, content="original"))

    post(client, message_event(message_id=message_id, content="adulterado"))

    assert Message.objects.get().content == "original"


# --- Outros eventos ---------------------------------------------------------


def test_evento_message_read_e_ignorado_sem_processamento(
    client: Client, scheduler: MagicMock
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

    response = post(client, payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": message_id}
    assert Message.objects.count() == 0
    assert Conversation.objects.count() == 0
    scheduler.assert_not_called()


def test_evento_desconhecido_responde_200_para_nao_gerar_reentrega(
    client: Client, scheduler: MagicMock
) -> None:
    """Um 4xx faria o provedor reenviar para sempre um evento que não tratamos."""

    response = post(client, {"event": "EVENTO_DO_FUTURO", "content": {}})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": None}


def test_evento_ignorado_sem_content_nao_quebra(
    client: Client, scheduler: MagicMock
) -> None:
    response = post(client, {"event": "MESSAGE_READ"})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# --- Payload inválido -------------------------------------------------------


def test_envelope_sem_event_e_rejeitado(client: Client, scheduler: MagicMock) -> None:
    response = post(client, {"content": {}})

    assert response.status_code == 400
    scheduler.assert_not_called()


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("message_id", "nao-e-uuid"),
        ("user_phone_number", "81982860171"),
        ("user_phone_number", "+55819"),
        ("message_content", ""),
        ("message_content", "   "),
        ("timestamp", "ontem"),
    ],
)
def test_conteudo_invalido_e_rejeitado_sem_persistir(
    client: Client, scheduler: MagicMock, campo: str, valor: str
) -> None:
    payload = message_event()
    payload["content"][campo] = valor

    response = post(client, payload)

    assert response.status_code == 400
    assert Message.objects.count() == 0
    scheduler.assert_not_called()


def test_campo_obrigatorio_ausente_e_rejeitado(
    client: Client, scheduler: MagicMock
) -> None:
    payload = message_event()
    del payload["content"]["message_content"]

    response = post(client, payload)

    assert response.status_code == 400
    assert Message.objects.count() == 0


def test_get_nao_e_permitido(client: Client) -> None:
    assert client.get(URL).status_code == 405
