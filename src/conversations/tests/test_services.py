from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from conversations.services import register_customer_message

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


def test_cria_conversa_e_mensagem_na_primeira_chamada() -> None:
    ingestion = register_customer_message(
        external_id=uuid4(),
        user_phone=PHONE,
        content="Olá",
        timestamp=NOW,
    )

    assert ingestion.created is True
    assert ingestion.conversation.user_phone == PHONE
    assert ingestion.message.role == MessageRole.CUSTOMER
    assert ingestion.conversation.last_message_at == NOW


def test_mesmo_external_id_nao_duplica_mensagem() -> None:
    external_id = uuid4()
    register_customer_message(
        external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW
    )

    ingestion = register_customer_message(
        external_id=external_id,
        user_phone=PHONE,
        content="conteúdo diferente",
        timestamp=NOW + timedelta(minutes=5),
    )

    assert ingestion.created is False
    assert Message.objects.count() == 1
    assert Message.objects.get().content == "Olá"
    # Duplicata não move a linha do tempo da conversa.
    assert Conversation.objects.get().last_message_at == NOW


def test_last_message_at_nao_retrocede_com_mensagem_atrasada() -> None:
    register_customer_message(
        external_id=uuid4(), user_phone=PHONE, content="segunda", timestamp=NOW
    )

    register_customer_message(
        external_id=uuid4(),
        user_phone=PHONE,
        content="chegou fora de ordem",
        timestamp=NOW - timedelta(minutes=1),
    )

    assert Conversation.objects.get().last_message_at == NOW


def test_telefones_diferentes_geram_conversas_diferentes() -> None:
    register_customer_message(
        external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW
    )
    register_customer_message(
        external_id=uuid4(), user_phone="+5581999998888", content="Oi", timestamp=NOW
    )

    assert Conversation.objects.count() == 2
