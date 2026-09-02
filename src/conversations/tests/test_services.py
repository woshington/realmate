from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from conversations.services import (
    add_recommendations,
    get_recent_messages,
    has_newer_customer_message,
    register_message,
    touch_last_message_at,
)
from properties.models import Property

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


# ---- Helpers ----------------------------------------------------------------

def create_conversation(
    user_phone: str = PHONE, last_message_at: datetime | None = None,
) -> Conversation:
    return Conversation.objects.create(user_phone=user_phone, last_message_at=last_message_at)


def create_message(
    conversation: Conversation,
    timestamp: datetime,
    role: str = MessageRole.CUSTOMER,
    content: str = "conteúdo",
) -> Message:
    return Message.objects.create(
        external_id=uuid4(),
        conversation=conversation,
        content=content,
        role=role,
        timestamp=timestamp,
    )


def create_property(code: str) -> Property:
    return Property.objects.create(
        code=code,
        transaction_type="aluguel",
        neighborhood="Boa Viagem",
        price=Decimal("2000"),
        bedrooms=2,
        address="Rua X, 100",
        description="Ótimo imóvel",
    )


# ---- register_message --------------------------------------------------------

class TestRegisterMessage:
    def test_cria_conversa_e_mensagem_na_primeira_chamada(self) -> None:
        ingestion = register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        assert ingestion.created is True
        assert ingestion.conversation.user_phone == PHONE
        assert ingestion.message.role == MessageRole.CUSTOMER
        assert ingestion.conversation.last_message_at == NOW

    def test_mesmo_external_id_nao_duplica_mensagem(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        ingestion = register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="conteúdo diferente",
            timestamp=NOW + timedelta(minutes=5),
        )

        assert ingestion.created is False
        assert Message.objects.count() == 1
        assert Message.objects.get().content == "Olá"

    def test_duplicata_nao_move_a_linha_do_tempo_da_conversa(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="conteúdo diferente",
            timestamp=NOW + timedelta(minutes=5),
        )

        assert Conversation.objects.get().last_message_at == NOW

    def test_last_message_at_nao_retrocede_com_mensagem_atrasada(self) -> None:
        register_message(
            external_id=uuid4(), user_phone=PHONE, content="segunda", timestamp=NOW,
        )

        register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="chegou fora de ordem",
            timestamp=NOW - timedelta(minutes=1),
        )

        assert Conversation.objects.get().last_message_at == NOW

    def test_telefones_diferentes_geram_conversas_diferentes(self) -> None:
        register_message(external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW)
        register_message(
            external_id=uuid4(), user_phone=OTHER_PHONE, content="Oi", timestamp=NOW,
        )

        assert Conversation.objects.count() == 2

    def test_mesmo_telefone_reaproveita_a_conversa_existente(self) -> None:
        register_message(external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW)
        register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="tudo bem?",
            timestamp=NOW + timedelta(minutes=1),
        )

        assert Conversation.objects.count() == 1
        assert Message.objects.count() == 2

    def test_role_default_e_customer_mas_pode_ser_sobrescrito(self) -> None:
        ingestion = register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="Resposta automática",
            timestamp=NOW,
            role=MessageRole.ASSISTANT,
        )

        assert ingestion.message.role == MessageRole.ASSISTANT


# ---- touch_last_message_at ----------------------------------------------------

class TestTouchLastMessageAt:
    def test_define_last_message_at_quando_ainda_nao_existe(self) -> None:
        conversation = create_conversation(last_message_at=None)

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_avanca_quando_timestamp_e_mais_recente(self) -> None:
        conversation = create_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW + timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW + timedelta(minutes=10)

    def test_nao_retrocede_com_timestamp_mais_antigo(self) -> None:
        conversation = create_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW - timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_nao_grava_quando_timestamp_e_igual_ao_atual(self) -> None:
        conversation = create_conversation(last_message_at=NOW)
        updated_at_before = conversation.updated_at

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.updated_at == updated_at_before


# ---- has_newer_customer_message ------------------------------------------------

class TestHasNewerCustomerMessage:
    def test_true_quando_existe_mensagem_de_cliente_mais_recente(self) -> None:
        conversation = create_conversation()
        reference = create_message(conversation, NOW)
        create_message(conversation, NOW + timedelta(minutes=1))

        result = has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        )

        assert result is True

    def test_false_quando_nao_ha_mensagem_mais_recente(self) -> None:
        conversation = create_conversation()
        reference = create_message(conversation, NOW)

        result = has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        )

        assert result is False

    def test_false_quando_a_mensagem_mais_recente_nao_e_do_cliente(self) -> None:
        conversation = create_conversation()
        reference = create_message(conversation, NOW)
        create_message(
            conversation, NOW + timedelta(minutes=1), role=MessageRole.ASSISTANT,
        )

        result = has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        )

        assert result is False

    def test_ignora_mensagens_mais_recentes_de_outra_conversa(self) -> None:
        conversation = create_conversation(user_phone=PHONE)
        other_conversation = create_conversation(user_phone=OTHER_PHONE)
        reference = create_message(conversation, NOW)
        create_message(other_conversation, NOW + timedelta(minutes=1))

        result = has_newer_customer_message(
            conversation_id=conversation.pk, message_id=reference.pk,
        )

        assert result is False


# ---- get_recent_messages -------------------------------------------------------

class TestGetRecentMessages:
    def test_retorna_as_mais_recentes_em_ordem_cronologica(self) -> None:
        conversation = create_conversation()
        older = create_message(conversation, NOW - timedelta(minutes=2), content="1")
        middle = create_message(conversation, NOW - timedelta(minutes=1), content="2")
        newest = create_message(conversation, NOW, content="3")

        result = get_recent_messages(conversation_id=conversation.pk, limit=2)

        assert [message.pk for message in result] == [middle.pk, newest.pk]

    def test_respeita_o_limite_pedido(self) -> None:
        conversation = create_conversation()
        for minute in range(5):
            create_message(conversation, NOW + timedelta(minutes=minute))

        result = get_recent_messages(conversation_id=conversation.pk, limit=3)

        assert len(result) == 3

    def test_before_message_id_pagina_para_mensagens_mais_antigas(self) -> None:
        conversation = create_conversation()
        first = create_message(conversation, NOW - timedelta(minutes=2), content="1")
        second = create_message(conversation, NOW - timedelta(minutes=1), content="2")
        third = create_message(conversation, NOW, content="3")

        result = get_recent_messages(
            conversation_id=conversation.pk, limit=10, before_message_id=third.pk,
        )

        assert [message.pk for message in result] == [first.pk, second.pk]

    def test_nao_mistura_mensagens_de_outra_conversa(self) -> None:
        conversation = create_conversation(user_phone=PHONE)
        other_conversation = create_conversation(user_phone=OTHER_PHONE)
        create_message(other_conversation, NOW)
        own_message = create_message(conversation, NOW)

        result = get_recent_messages(conversation_id=conversation.pk, limit=10)

        assert [message.pk for message in result] == [own_message.pk]

    def test_lista_vazia_quando_conversa_nao_tem_mensagens(self) -> None:
        conversation = create_conversation()

        result = get_recent_messages(conversation_id=conversation.pk, limit=10)

        assert result == []


# ---- add_recommendations -------------------------------------------------------

class TestAddRecommendations:
    def test_associa_imoveis_encontrados_pelo_codigo(self) -> None:
        conversation = create_conversation()
        first_property = create_property("IMV-001")
        second_property = create_property("IMV-002")

        add_recommendations(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "IMV-002"],
        )

        assert set(conversation.recommended_properties.all()) == {
            first_property, second_property,
        }

    def test_ignora_codigos_que_nao_existem(self) -> None:
        conversation = create_conversation()
        existing_property = create_property("IMV-001")

        add_recommendations(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "CODIGO-INEXISTENTE"],
        )

        assert list(conversation.recommended_properties.all()) == [existing_property]

    def test_lista_vazia_nao_adiciona_nada(self) -> None:
        conversation = create_conversation()
        create_property("IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=[])

        assert conversation.recommended_properties.count() == 0

    def test_chamadas_repetidas_nao_duplicam_a_associacao(self) -> None:
        conversation = create_conversation()
        create_property("IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])
        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])

        assert conversation.recommended_properties.count() == 1