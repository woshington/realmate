"""Serviços de conversa.

Camada onde mora a idempotência de ingestão (mesma mensagem entregue duas vezes
não vira duas conversas) e a montagem do histórico que alimenta a IA.
"""

from datetime import timedelta
from typing import Callable
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

from .conftest import NOW, OTHER_PHONE, PHONE

pytestmark = pytest.mark.django_db

MakeConversation = Callable[..., Conversation]
MakeMessage = Callable[..., Message]
MakeProperty = Callable[..., Property]


class TestRegisterMessageIdempotencia:
    """O provedor de mensageria reentrega; o banco não pode duplicar."""

    def test_cria_conversa_e_mensagem_na_primeira_chamada(self) -> None:
        ingestion = register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        assert ingestion.created is True
        assert ingestion.conversation.user_phone == PHONE
        assert ingestion.message.role == MessageRole.CUSTOMER
        assert ingestion.conversation.last_message_at == NOW

    def test_mesmo_external_id_nao_duplica_a_mensagem(self) -> None:
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

    def test_reentrega_nao_sobrescreve_o_conteudo_original(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="adulterado",
            timestamp=NOW,
        )

        assert Message.objects.get().content == "Olá"

    def test_reentrega_nao_move_a_linha_do_tempo_da_conversa(self) -> None:
        external_id = uuid4()
        register_message(
            external_id=external_id, user_phone=PHONE, content="Olá", timestamp=NOW,
        )

        register_message(
            external_id=external_id,
            user_phone=PHONE,
            content="Olá",
            timestamp=NOW + timedelta(minutes=5),
        )

        assert Conversation.objects.get().last_message_at == NOW


class TestRegisterMessageConversa:
    def test_mesmo_telefone_reaproveita_a_conversa(self) -> None:
        register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )
        register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="tudo bem?",
            timestamp=NOW + timedelta(minutes=1),
        )

        assert Conversation.objects.count() == 1
        assert Message.objects.count() == 2

    def test_telefones_diferentes_geram_conversas_diferentes(self) -> None:
        register_message(
            external_id=uuid4(), user_phone=PHONE, content="Olá", timestamp=NOW,
        )
        register_message(
            external_id=uuid4(), user_phone=OTHER_PHONE, content="Oi", timestamp=NOW,
        )

        assert Conversation.objects.count() == 2

    def test_o_papel_padrao_e_cliente_mas_pode_ser_sobrescrito(self) -> None:
        ingestion = register_message(
            external_id=uuid4(),
            user_phone=PHONE,
            content="Resposta automática",
            timestamp=NOW,
            role=MessageRole.ASSISTANT,
        )

        assert ingestion.message.role == MessageRole.ASSISTANT


class TestTouchLastMessageAt:
    """``last_message_at`` só anda para frente."""

    def test_define_quando_ainda_nao_existe(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=None)

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_avanca_com_timestamp_mais_recente(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW + timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW + timedelta(minutes=10)

    def test_nao_retrocede_com_mensagem_que_chegou_fora_de_ordem(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)

        touch_last_message_at(conversation, NOW - timedelta(minutes=10))

        conversation.refresh_from_db()
        assert conversation.last_message_at == NOW

    def test_nao_grava_quando_o_timestamp_e_igual_ao_atual(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation(last_message_at=NOW)
        updated_at_antes = conversation.updated_at

        touch_last_message_at(conversation, NOW)

        conversation.refresh_from_db()
        assert conversation.updated_at == updated_at_antes


class TestHasNewerCustomerMessage:
    """A pergunta que decide se o debounce ainda vale."""

    def test_true_quando_o_cliente_mandou_outra_mensagem(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        referencia = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(minutes=1))

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=referencia.pk,
        ) is True

    def test_false_quando_nao_ha_mensagem_mais_recente(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        referencia = make_message(conversation, NOW)

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=referencia.pk,
        ) is False

    def test_false_quando_a_mais_recente_e_do_assistente(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        referencia = make_message(conversation, NOW)
        make_message(
            conversation, NOW + timedelta(minutes=1), role=MessageRole.ASSISTANT,
        )

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=referencia.pk,
        ) is False

    def test_ignora_mensagem_mais_recente_de_outra_conversa(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        outra = make_conversation(user_phone=OTHER_PHONE)
        referencia = make_message(conversation, NOW)
        make_message(outra, NOW + timedelta(minutes=1))

        assert has_newer_customer_message(
            conversation_id=conversation.pk, message_id=referencia.pk,
        ) is False


class TestGetRecentMessages:
    """Histórico enviado ao modelo: recente, em ordem e sem vazamento."""

    def test_devolve_as_mais_recentes_em_ordem_cronologica(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=2), content="1")
        meio = make_message(conversation, NOW - timedelta(minutes=1), content="2")
        ultima = make_message(conversation, NOW, content="3")

        result = get_recent_messages(conversation_id=conversation.pk, limit=2)

        assert [message.pk for message in result] == [meio.pk, ultima.pk]

    def test_respeita_o_limite_pedido(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        for minute in range(5):
            make_message(conversation, NOW + timedelta(minutes=minute))

        result = get_recent_messages(conversation_id=conversation.pk, limit=3)

        assert len(result) == 3

    def test_before_message_id_exclui_o_gatilho_do_historico(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        primeira = make_message(conversation, NOW - timedelta(minutes=2), content="1")
        segunda = make_message(conversation, NOW - timedelta(minutes=1), content="2")
        gatilho = make_message(conversation, NOW, content="3")

        result = get_recent_messages(
            conversation_id=conversation.pk, limit=10, before_message_id=gatilho.pk,
        )

        assert [message.pk for message in result] == [primeira.pk, segunda.pk]

    def test_nao_mistura_mensagens_de_outra_conversa(
        self, make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        outra = make_conversation(user_phone=OTHER_PHONE)
        make_message(outra, NOW)
        propria = make_message(conversation, NOW)

        result = get_recent_messages(conversation_id=conversation.pk, limit=10)

        assert [message.pk for message in result] == [propria.pk]

    def test_conversa_sem_mensagens_devolve_lista_vazia(
        self, make_conversation: MakeConversation,
    ) -> None:
        conversation = make_conversation()

        assert get_recent_messages(conversation_id=conversation.pk, limit=10) == []


class TestAddRecommendations:
    def test_associa_os_imoveis_pelo_codigo(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        primeiro = make_property(code="IMV-001")
        segundo = make_property(code="IMV-002")

        add_recommendations(
            conversation_id=conversation.pk, property_codes=["IMV-001", "IMV-002"],
        )

        assert set(conversation.recommended_properties.all()) == {primeiro, segundo}

    def test_ignora_codigo_que_nao_existe(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        existente = make_property(code="IMV-001")

        add_recommendations(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "CODIGO-INEXISTENTE"],
        )

        assert list(conversation.recommended_properties.all()) == [existente]

    def test_lista_vazia_nao_associa_nada(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        make_property(code="IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=[])

        assert conversation.recommended_properties.count() == 0

    def test_chamadas_repetidas_nao_duplicam_a_associacao(
        self, make_conversation: MakeConversation, make_property: MakeProperty,
    ) -> None:
        conversation = make_conversation()
        make_property(code="IMV-001")

        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])
        add_recommendations(conversation_id=conversation.pk, property_codes=["IMV-001"])

        assert conversation.recommended_properties.count() == 1
