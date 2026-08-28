from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.db import IntegrityError

from conversations.enums import MessageRole
from conversations.models import Conversation, Message, PropertyRecommendation
from properties.enums import TransactionType
from properties.models import Property

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def conversation() -> Conversation:
    return Conversation.objects.create(user_phone="+5581982860171")


@pytest.fixture
def property_imv001() -> Property:
    return Property.objects.create(
        code="IMV-001",
        transaction_type=TransactionType.RENT,
        neighborhood="Boa Viagem",
        price=Decimal("2500.00"),
        bedrooms=2,
    )


def test_historico_e_ordenado_do_mais_antigo_para_o_mais_recente(
    conversation: Conversation,
) -> None:
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content="resposta",
        timestamp=NOW + timedelta(seconds=5),
    )
    Message.objects.create(
        conversation=conversation,
        role=MessageRole.CUSTOMER,
        content="pergunta",
        timestamp=NOW,
    )

    assert [m.content for m in conversation.messages.all()] == ["pergunta", "resposta"]


def test_mensagem_do_assistente_recebe_external_id_proprio(
    conversation: Conversation,
) -> None:
    message = Message.objects.create(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content="Olá!",
        timestamp=NOW,
    )

    assert message.external_id is not None


def test_imovel_nao_pode_ser_recomendado_duas_vezes_na_mesma_conversa(
    conversation: Conversation, property_imv001: Property
) -> None:
    PropertyRecommendation.objects.create(
        conversation=conversation, property=property_imv001
    )

    with pytest.raises(IntegrityError):
        PropertyRecommendation.objects.create(
            conversation=conversation, property=property_imv001
        )


def test_mesmo_imovel_pode_ser_recomendado_em_conversas_diferentes(
    conversation: Conversation, property_imv001: Property
) -> None:
    other = Conversation.objects.create(user_phone="+5581999998888")

    PropertyRecommendation.objects.create(
        conversation=conversation, property=property_imv001
    )
    PropertyRecommendation.objects.create(conversation=other, property=property_imv001)

    assert property_imv001.conversations.count() == 2
