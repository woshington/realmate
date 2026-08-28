from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from assistant.schemas import AgentReply, PropertyOutput
from conversations.enums import MessageRole
from conversations.models import Message
from conversations.services import (
    get_recent_messages,
    has_newer_customer_message,
    register_customer_message,
)
from conversations.tasks import process_conversation, schedule_conversation_processing

pytestmark = pytest.mark.django_db

PHONE = "+5581982860171"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


def ingest(content: str, offset_seconds: int) -> Message:
    return register_customer_message(
        external_id=uuid4(),
        user_phone=PHONE,
        content=content,
        timestamp=NOW + timedelta(seconds=offset_seconds),
    ).message


def reply_from_assistant(
    conversation_id: int, content: str, offset_seconds: int
) -> Message:
    return Message.objects.create(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=content,
        timestamp=NOW + timedelta(seconds=offset_seconds),
    )


@pytest.fixture
def fake_agent() -> Any:
    """Substitui o agente real: nenhum teste deve chamar a OpenAI."""

    agent = MagicMock()
    agent.run_sync.return_value = MagicMock(
        output=AgentReply(message="Olá! Como posso ajudar?", recommended_properties=[])
    )
    with patch("assistant.agent.get_agent", return_value=agent):
        yield agent


# --- debounce ---------------------------------------------------------------


def test_agendamento_usa_a_janela_de_debounce() -> None:
    with patch("conversations.tasks.process_conversation.apply_async") as apply_async:
        schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

    apply_async.assert_called_once_with(
        kwargs={"conversation_id": 1, "trigger_message_id": 2},
        countdown=settings.DEBOUNCE_WINDOW_SECONDS,
    )
    # A janela de 10s é requisito do desafio, não um detalhe de configuração.
    assert settings.DEBOUNCE_WINDOW_SECONDS == 10


def test_rajada_de_mensagens_chama_a_ia_uma_unica_vez(fake_agent: Any) -> None:
    burst = [ingest("Oi", 0), ingest("bom dia", 2), ingest("procuro apartamento", 4)]

    for message in burst:
        process_conversation(
            conversation_id=message.conversation.pk, trigger_message_id=message.pk
        )

    fake_agent.run_sync.assert_called_once()
    # A chamada que sobreviveu é a da última mensagem da rajada.
    assert fake_agent.run_sync.call_args.args[0] == "procuro apartamento"


def test_rajada_com_o_mesmo_timestamp_ainda_chama_a_ia_uma_vez(
    fake_agent: Any,
) -> None:
    """Regressão: mensagens de uma rajada podem empatar no horário de origem.

    O provedor manda o timestamp com granularidade de segundo, então "Oi" e
    "bom dia" digitados juntos chegam com o mesmo valor. Se o debounce comparar
    por ``timestamp``, nenhuma é mais nova que a outra e todas processam.
    """

    burst = [ingest("Oi", 0), ingest("bom dia", 0), ingest("procuro apartamento", 0)]
    assert len({message.timestamp for message in burst}) == 1

    for message in burst:
        process_conversation(
            conversation_id=message.conversation.pk, trigger_message_id=message.pk
        )

    fake_agent.run_sync.assert_called_once()
    assert fake_agent.run_sync.call_args.args[0] == "procuro apartamento"


def test_resposta_do_assistente_nao_supera_o_debounce() -> None:
    message = ingest("Oi", 0)
    reply_from_assistant(message.conversation.pk, "Olá!", 1)

    assert not has_newer_customer_message(
        conversation_id=message.conversation.pk, message_id=message.pk
    )


def test_mensagem_de_outra_conversa_nao_supera_o_debounce() -> None:
    message = ingest("Oi", 0)
    register_customer_message(
        external_id=uuid4(),
        user_phone="+5581999998888",
        content="Oi de outro cliente",
        timestamp=NOW + timedelta(seconds=1),
    )

    assert not has_newer_customer_message(
        conversation_id=message.conversation.pk, message_id=message.pk
    )


# --- chamada da IA ----------------------------------------------------------


def test_mensagem_que_disparou_a_task_vira_o_prompt(fake_agent: Any) -> None:
    message = ingest("Quero alugar em Boa Viagem", 0)

    process_conversation(
        conversation_id=message.conversation.pk, trigger_message_id=message.pk
    )

    assert fake_agent.run_sync.call_args.args[0] == "Quero alugar em Boa Viagem"


def test_conversa_e_passada_como_dependencia_do_agente(fake_agent: Any) -> None:
    message = ingest("Oi", 0)

    process_conversation(
        conversation_id=message.conversation.pk, trigger_message_id=message.pk
    )

    assert fake_agent.run_sync.call_args.kwargs["deps"] == str(message.conversation.pk)


def test_historico_vai_em_ordem_cronologica_e_sem_a_mensagem_do_prompt(
    fake_agent: Any,
) -> None:
    first = ingest("Oi", 0)
    conversation_id = first.conversation.pk
    reply_from_assistant(conversation_id, "Olá! Como posso ajudar?", 5)
    trigger = ingest("Quero alugar em Boa Viagem", 10)

    process_conversation(
        conversation_id=conversation_id, trigger_message_id=trigger.pk
    )

    history = fake_agent.run_sync.call_args.kwargs["message_history"]
    assert [type(entry) for entry in history] == [ModelRequest, ModelResponse]
    assert [entry.parts[0].content for entry in history] == [
        "Oi",
        "Olá! Como posso ajudar?",
    ]


def test_historico_respeita_o_limite_configurado(fake_agent: Any) -> None:
    limit = settings.AGENT_HISTORY_MESSAGE_LIMIT
    older = [ingest(f"mensagem {index}", index) for index in range(limit + 5)]
    trigger = ingest("última", 100)

    process_conversation(
        conversation_id=trigger.conversation.pk, trigger_message_id=trigger.pk
    )

    history = fake_agent.run_sync.call_args.kwargs["message_history"]
    assert len(history) == limit
    # Corta as mais antigas, mantendo as últimas N anteriores ao prompt.
    assert [entry.parts[0].content for entry in history] == [
        message.content for message in older[-limit:]
    ]


def test_historico_de_outra_conversa_nao_vaza(fake_agent: Any) -> None:
    message = ingest("Oi", 0)
    register_customer_message(
        external_id=uuid4(),
        user_phone="+5581999998888",
        content="conversa alheia",
        timestamp=NOW - timedelta(minutes=5),
    )

    process_conversation(
        conversation_id=message.conversation.pk, trigger_message_id=message.pk
    )

    assert fake_agent.run_sync.call_args.kwargs["message_history"] == []


def test_task_superada_nao_chama_a_ia(fake_agent: Any) -> None:
    first = ingest("Oi", 0)
    ingest("bom dia", 2)

    process_conversation(
        conversation_id=first.conversation.pk, trigger_message_id=first.pk
    )

    fake_agent.run_sync.assert_not_called()


def test_resposta_da_ia_e_registrada_no_log(
    fake_agent: Any, caplog: pytest.LogCaptureFixture
) -> None:
    fake_agent.run_sync.return_value = MagicMock(
        output=AgentReply(
            message="Encontrei 2 opções",
            recommended_properties=[
                PropertyOutput(
                    code="IMV-001",
                    price=2500,
                    neighborhood="Boa Viagem",
                    bedrooms=2,
                    address="Rua dos Navegantes, 150",
                    description="Apartamento com 2 quartos",
                )
            ],
        )
    )
    message = ingest("Quero alugar em Boa Viagem até 3000", 0)

    with caplog.at_level("INFO", logger="conversations.tasks"):
        process_conversation(
            conversation_id=message.conversation.pk, trigger_message_id=message.pk
        )

    assert "IMV-001" in caplog.text


# --- montagem do histórico --------------------------------------------------


def test_get_recent_messages_devolve_do_mais_antigo_para_o_mais_recente() -> None:
    first = ingest("primeira", 0)
    ingest("segunda", 5)

    recent = get_recent_messages(
        conversation_id=first.conversation.pk,
        limit=settings.AGENT_HISTORY_MESSAGE_LIMIT,
    )

    assert [message.content for message in recent] == ["primeira", "segunda"]


def test_get_recent_messages_mantem_as_ultimas_ao_cortar() -> None:
    messages = [ingest(f"mensagem {index}", index) for index in range(5)]

    recent = get_recent_messages(
        conversation_id=messages[-1].conversation.pk, limit=2
    )

    assert [entry.content for entry in recent] == ["mensagem 3", "mensagem 4"]


def test_to_model_messages_mapeia_papeis_para_o_sdk() -> None:
    from assistant.history import to_model_messages

    message = ingest("Oi", 0)
    assistant_reply = reply_from_assistant(message.conversation.pk, "Olá!", 1)

    history = to_model_messages([message, assistant_reply])

    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], UserPromptPart)
    assert history[0].parts[0].timestamp == message.timestamp
    assert isinstance(history[1], ModelResponse)
    assert isinstance(history[1].parts[0], TextPart)
    assert history[1].parts[0].content == "Olá!"
