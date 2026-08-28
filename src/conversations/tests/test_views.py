from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from conversations.enums import MessageRole
from conversations.models import Conversation, Message, PropertyRecommendation
from properties.enums import TransactionType
from properties.models import Property

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


def url_for(user_phone: str) -> str:
    return reverse("conversations:messages", kwargs={"user_phone": user_phone})


@pytest.fixture
def conversation() -> Conversation:
    conversation = Conversation.objects.create(user_phone=PHONE)
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.CUSTOMER,
        content="Olá, estou procurando um apartamento para alugar em Boa Viagem",
        timestamp=NOW,
    )
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content="Olá! Encontrei 2 imóveis que podem atender você...",
        timestamp=NOW + timedelta(seconds=5),
    )
    return conversation


def make_property(code: str) -> Property:
    return Property.objects.create(
        code=code,
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        price=Decimal("2500.00"),
        bedrooms=2,
    )


def test_retorna_historico_no_formato_do_contrato(
    client: Client, conversation: Conversation
) -> None:
    response = client.get(url_for(PHONE))

    assert response.status_code == 200
    assert response.json() == {
        "user_phone": PHONE,
        "properties_found": [],
        "messages": [
            {
                "role": "customer",
                "content": "Olá, estou procurando um apartamento para alugar em Boa Viagem",
                "timestamp": "2026-06-02T10:00:00Z",
            },
            {
                "role": "assistant",
                "content": "Olá! Encontrei 2 imóveis que podem atender você...",
                "timestamp": "2026-06-02T10:00:05Z",
            },
        ],
    }


def test_mensagens_saem_do_mais_antigo_para_o_mais_recente(
    client: Client, conversation: Conversation
) -> None:
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.CUSTOMER,
        content="mais antiga, inserida por último",
        timestamp=NOW - timedelta(minutes=1),
    )

    timestamps = [m["timestamp"] for m in client.get(url_for(PHONE)).json()["messages"]]

    assert timestamps == sorted(timestamps)
    assert timestamps[0] == "2026-06-02T09:59:00Z"


def test_properties_found_lista_codigos_na_ordem_da_recomendacao(
    client: Client, conversation: Conversation
) -> None:
    for code in ("IMV-001", "C011"):
        PropertyRecommendation.objects.create(
            conversation=conversation, property=make_property(code)
        )

    assert client.get(url_for(PHONE)).json()["properties_found"] == ["IMV-001", "C011"]


def test_recomendacoes_de_outra_conversa_nao_vazam(
    client: Client, conversation: Conversation
) -> None:
    other = Conversation.objects.create(user_phone="+5581999998888")
    PropertyRecommendation.objects.create(
        conversation=other, property=make_property("IMV-999")
    )

    assert client.get(url_for(PHONE)).json()["properties_found"] == []


def test_timestamp_e_convertido_para_utc_com_sufixo_z(
    client: Client, conversation: Conversation
) -> None:
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.CUSTOMER,
        content="enviada em -03:00",
        timestamp=datetime(2026, 6, 2, 8, 0, tzinfo=timezone(timedelta(hours=-3))),
    )

    timestamps = [m["timestamp"] for m in client.get(url_for(PHONE)).json()["messages"]]

    assert "2026-06-02T11:00:00Z" in timestamps


def test_conversa_sem_mensagens_retorna_listas_vazias(client: Client) -> None:
    Conversation.objects.create(user_phone=PHONE)

    assert client.get(url_for(PHONE)).json() == {
        "user_phone": PHONE,
        "properties_found": [],
        "messages": [],
    }


def test_telefone_desconhecido_retorna_404(client: Client) -> None:
    assert client.get(url_for("+5581900000000")).status_code == 404


def test_telefone_sem_o_mais_resolve_a_mesma_conversa(
    client: Client, conversation: Conversation
) -> None:
    response = client.get(url_for(PHONE.removeprefix("+")))

    assert response.status_code == 200
    assert response.json()["user_phone"] == PHONE


def test_telefone_em_formato_invalido_nao_casa_a_rota(client: Client) -> None:
    assert client.get("/api/conversations/abc/messages").status_code == 404


def test_metodo_nao_permitido(client: Client, conversation: Conversation) -> None:
    assert client.post(url_for(PHONE)).status_code == 405
