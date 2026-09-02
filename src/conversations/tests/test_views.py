"""``GET /api/conversations/{user_phone}/messages``.

Contrato de leitura do histórico: é por aqui que o avaliador do desafio confere
o que o assistente respondeu e quais imóveis foram apresentados.
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


class TestFormatoDaResposta:
    def test_devolve_o_historico_no_formato_do_contrato(
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

    def test_conversa_sem_mensagens_devolve_listas_vazias(
        self, client: Client, make_conversation: MakeConversation,
    ) -> None:
        make_conversation()

        assert client.get(url_for(PHONE)).json() == {
            "user_phone": PHONE,
            "properties_found": [],
            "messages": [],
        }

    def test_timestamp_sai_em_utc_com_sufixo_z(
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


class TestOrdenacao:
    def test_mensagens_saem_da_mais_antiga_para_a_mais_recente(
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

    def test_properties_found_segue_a_ordem_da_recomendacao(
        self, client: Client, conversation: Conversation, make_property: MakeProperty,
    ) -> None:
        for code in ("IMV-001", "C011"):
            PropertyRecommendation.objects.create(
                conversation=conversation, property=make_property(code=code),
            )

        found = client.get(url_for(PHONE)).json()["properties_found"]

        assert found == ["IMV-001", "C011"]


class TestIsolamentoEntreConversas:
    def test_recomendacao_de_outra_conversa_nao_vaza(
        self, client: Client, conversation: Conversation,
        make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        outra = make_conversation(user_phone=OTHER_PHONE)
        PropertyRecommendation.objects.create(
            conversation=outra, property=make_property(code="IMV-999"),
        )

        assert client.get(url_for(PHONE)).json()["properties_found"] == []

    def test_mensagem_de_outra_conversa_nao_vaza(
        self, client: Client, conversation: Conversation,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        outra = make_conversation(user_phone=OTHER_PHONE)
        make_message(outra, NOW, content="segredo alheio")

        conteudos = [
            message["content"]
            for message in client.get(url_for(PHONE)).json()["messages"]
        ]

        assert "segredo alheio" not in conteudos


class TestResolucaoDoTelefone:
    def test_telefone_sem_o_mais_resolve_a_mesma_conversa(
        self, client: Client, conversation: Conversation,
    ) -> None:
        """Alguns clientes omitem o ``+`` ao montar a URL."""

        response = client.get(url_for(PHONE.removeprefix("+")))

        assert response.status_code == 200
        assert response.json()["user_phone"] == PHONE

    def test_telefone_desconhecido_devolve_404(self, client: Client) -> None:
        assert client.get(url_for("+5581900000000")).status_code == 404

    def test_telefone_em_formato_invalido_nao_casa_a_rota(self, client: Client) -> None:
        assert client.get("/api/conversations/abc/messages").status_code == 404


class TestMetodosPermitidos:
    def test_a_rota_e_somente_leitura(
        self, client: Client, conversation: Conversation,
    ) -> None:
        assert client.post(url_for(PHONE)).status_code == 405
