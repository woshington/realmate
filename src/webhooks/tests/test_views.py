"""Contrato do webhook de mensageria.

O webhook é a única porta de entrada de mensagem do sistema e conversa com um
provedor externo que reentrega. As garantias testadas aqui — idempotência,
filtro de evento e formato da resposta — são o que impede uma reentrega de virar
uma segunda cobrança de IA.
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


class TestMensagemRecebida:
    def test_responde_no_formato_do_contrato(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        payload = message_event()

        response = post(payload)

        assert response.status_code == 200
        assert response.json() == {
            "status": "accepted",
            "message_id": payload["content"]["message_id"],
        }

    def test_persiste_a_conversa_e_a_mensagem(
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

    def test_agenda_o_processamento_da_conversa(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event())

        scheduler.assert_called_once_with(
            conversation_id=Conversation.objects.get().pk,
            trigger_message_id=Message.objects.get().pk,
        )

    def test_a_ia_nao_e_chamada_dentro_do_request(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        """A view precisa devolver 200 antes do timeout do provedor."""

        post(message_event())

        assert Message.objects.filter(role=MessageRole.ASSISTANT).count() == 0

    def test_segunda_mensagem_do_mesmo_telefone_reusa_a_conversa(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event(content="Oi"))
        post(message_event(content="bom dia", timestamp=NOW + timedelta(seconds=3)))

        assert Conversation.objects.count() == 1
        assert Message.objects.count() == 2
        assert scheduler.call_count == 2

    def test_telefones_diferentes_geram_conversas_diferentes(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        post(message_event(phone=PHONE))
        post(message_event(phone=OTHER_PHONE))

        assert Conversation.objects.count() == 2


class TestIdempotencia:
    """Reentrega do provedor não pode custar uma segunda resposta."""

    def test_o_mesmo_message_id_e_ignorado(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))

        response = post(message_event(message_id=message_id))

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": message_id}
        assert Message.objects.count() == 1

    def test_a_reentrega_nao_agenda_um_segundo_processamento(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))
        post(message_event(message_id=message_id))

        scheduler.assert_called_once()

    def test_a_reentrega_com_conteudo_diferente_nao_sobrescreve_o_original(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id, content="original"))

        post(message_event(message_id=message_id, content="adulterado"))

        assert Message.objects.get().content == "original"

    def test_a_reentrega_nao_cria_uma_segunda_conversa(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        message_id = str(uuid4())
        post(message_event(message_id=message_id))
        post(message_event(message_id=message_id))

        assert Conversation.objects.count() == 1


class TestEventosIgnorados:
    """Evento que não sabemos tratar sai com 200 e sem efeito colateral."""

    def test_message_read_e_ignorado_sem_processamento(
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

    def test_evento_desconhecido_responde_200_para_nao_gerar_reentrega(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        """Um 4xx faria o provedor reenviar para sempre um evento que não tratamos."""

        response = post({"event": "EVENTO_DO_FUTURO", "content": {}})

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": None}

    def test_evento_ignorado_sem_content_nao_quebra(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"event": "MESSAGE_READ"})

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_evento_ignorado_com_message_id_nao_textual_nao_quebra(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"event": "MESSAGE_READ", "content": {"message_id": 42}})

        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "message_id": None}


class TestPayloadInvalido:
    """Payload malformado é erro do chamador: 400 e nada persistido."""

    def test_envelope_sem_event_e_rejeitado(
        self, post: Post, scheduler: MagicMock,
    ) -> None:
        response = post({"content": {}})

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
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
        campo: str, valor: str,
    ) -> None:
        payload = message_event()
        payload["content"][campo] = valor

        response = post(payload)

        assert response.status_code == 400
        assert Message.objects.count() == 0
        scheduler.assert_not_called()

    def test_campo_obrigatorio_ausente_e_rejeitado(
        self, post: Post, message_event: MessageEvent, scheduler: MagicMock,
    ) -> None:
        payload = message_event()
        del payload["content"]["message_content"]

        response = post(payload)

        assert response.status_code == 400
        assert Message.objects.count() == 0


class TestMetodosPermitidos:
    def test_get_nao_e_permitido(self, client: Client) -> None:
        assert client.get(URL).status_code == 405
