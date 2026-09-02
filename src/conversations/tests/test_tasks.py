"""Task que responde a conversa.

O que esta task protege não é a qualidade da resposta — isso é do agente — e sim
o que acontece em volta dela: esperar o cliente terminar de digitar (debounce),
não responder duas vezes a mesma mensagem (lock), e nunca deixar o cliente sem
resposta quando a IA falha (fallback).
"""

from datetime import timedelta
from typing import Any, Callable
from unittest import mock

import pytest
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError

from django.core.cache import cache

from assistant import FALLBACK_MESSAGE
from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from conversations.tasks import process_conversation, schedule_conversation_processing

from .conftest import NOW, OTHER_PHONE, PHONE, FakeRunner, reply_with

pytestmark = pytest.mark.django_db

MakeConversation = Callable[..., Conversation]
MakeMessage = Callable[..., Message]


def lock_key(conversation_id: int, trigger_message_id: int) -> str:
    return f"lock:{conversation_id}-{trigger_message_id}"


def run_task(conversation: Conversation, trigger: Message) -> None:
    process_conversation(
        conversation_id=conversation.pk,
        trigger_message_id=trigger.pk,
    )


def assistant_messages() -> list[Message]:
    return list(Message.objects.filter(role=MessageRole.ASSISTANT))


class TestDebounce:
    """O cliente manda três mensagens seguidas; a IA responde uma vez, no fim."""

    def test_o_processamento_e_agendado_com_a_janela_configurada(
        self, settings: Any,
    ) -> None:
        settings.DEBOUNCE_WINDOW_SECONDS = 30

        with mock.patch.object(process_conversation, "apply_async") as apply_async:
            schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

        apply_async.assert_called_once_with(
            kwargs={"conversation_id": 1, "trigger_message_id": 2},
            countdown=30,
        )

    def test_a_janela_vem_das_settings_e_nao_de_um_valor_fixo(
        self, settings: Any,
    ) -> None:
        settings.DEBOUNCE_WINDOW_SECONDS = 5

        with mock.patch.object(process_conversation, "apply_async") as apply_async:
            schedule_conversation_processing(conversation_id=1, trigger_message_id=2)

        assert apply_async.call_args.kwargs["countdown"] == 5

    def test_mensagem_superada_por_outra_mais_recente_nao_chama_a_ia(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(seconds=3))

        run_task(conversation, trigger)

        assert runner.agent_was_built is False
        assert runner.ran is False
        assert assistant_messages() == []

    def test_somente_a_ultima_mensagem_da_rajada_e_respondida(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        primeira = make_message(conversation, NOW, content="quero alugar")
        segunda = make_message(conversation, NOW + timedelta(seconds=2), content="em Boa Viagem")
        ultima = make_message(conversation, NOW + timedelta(seconds=4), content="até 3000")

        for trigger in (primeira, segunda, ultima):
            run_task(conversation, trigger)

        assert len(assistant_messages()) == 1

    def test_mensagem_mais_recente_do_assistente_nao_supera_o_gatilho(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Só mensagem *do cliente* reinicia a espera."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(
            conversation, NOW + timedelta(seconds=3), role=MessageRole.ASSISTANT,
        )

        run_task(conversation, trigger)

        assert runner.ran is True

    def test_mensagem_de_outra_conversa_nao_supera_o_gatilho(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        outra = make_conversation(user_phone=OTHER_PHONE)
        trigger = make_message(conversation, NOW)
        make_message(outra, NOW + timedelta(minutes=1))

        run_task(conversation, trigger)

        assert len(assistant_messages()) == 1


class TestIdempotencia:
    """A mesma mensagem não pode virar duas respostas — nem dois custos de IA."""

    def test_execucao_concorrente_do_mesmo_gatilho_e_descartada(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        cache.set(lock_key(conversation.pk, trigger.pk), "true", 15)

        run_task(conversation, trigger)

        assert runner.agent_was_built is False
        assert assistant_messages() == []

    def test_o_lock_e_liberado_apos_uma_execucao_bem_sucedida(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_o_lock_e_liberado_quando_a_mensagem_foi_superada(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        make_message(conversation, NOW + timedelta(minutes=1))

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_o_lock_e_liberado_mesmo_quando_a_ia_falha(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("resposta inválida"))

        run_task(conversation, trigger)

        assert cache.get(lock_key(conversation.pk, trigger.pk)) is None

    def test_o_lock_e_por_gatilho_e_nao_por_conversa(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        cache.set(lock_key(conversation.pk, trigger.pk + 999), "true", 15)

        run_task(conversation, trigger)

        assert runner.ran is True


class TestEntradaEnviadaAoAgente:
    def test_envia_o_historico_recente_antes_da_mensagem_do_gatilho(
        self, runner: FakeRunner, settings: Any,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        settings.AGENT_HISTORY_MESSAGE_LIMIT = 30
        conversation = make_conversation()
        make_message(conversation, NOW - timedelta(minutes=2), content="oi")
        make_message(
            conversation, NOW - timedelta(minutes=1),
            role=MessageRole.ASSISTANT, content="olá!",
        )
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert runner.input_sent == [
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "olá!"},
            {"role": "user", "content": "quero alugar"},
        ]

    def test_respeita_o_limite_de_historico_das_settings(
        self, runner: FakeRunner, settings: Any,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        settings.AGENT_HISTORY_MESSAGE_LIMIT = 2
        conversation = make_conversation()
        for minute in range(5):
            make_message(conversation, NOW - timedelta(minutes=10 - minute))
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert len(runner.input_sent) == 3  # 2 do histórico + o gatilho

    def test_passa_a_conversa_no_contexto_das_tools(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)

        run_task(conversation, trigger)

        assert runner.deps_used.conversation_id == conversation.pk

    def test_nao_mistura_o_historico_de_outra_conversa(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation(user_phone=PHONE)
        outra = make_conversation(user_phone=OTHER_PHONE)
        make_message(outra, NOW - timedelta(minutes=1), content="segredo alheio")
        trigger = make_message(conversation, NOW, content="quero alugar")

        run_task(conversation, trigger)

        assert runner.input_sent == [{"role": "user", "content": "quero alugar"}]


class TestRespostaDoAgente:
    def test_grava_a_resposta_como_mensagem_do_assistente(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Temos ótimas opções."))

        run_task(conversation, trigger)

        resposta = Message.objects.get(role=MessageRole.ASSISTANT)
        assert resposta.content == "Temos ótimas opções."
        assert resposta.conversation == conversation
        assert resposta.timestamp == trigger.timestamp

    def test_registra_os_imoveis_recomendados_na_resposta(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Encontrei estes.", "IMV-001", "IMV-002"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk,
            property_codes=["IMV-001", "IMV-002"],
        )

    def test_usa_os_codigos_apresentados_pela_tool_quando_a_resposta_nao_lista(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """O imóvel foi mostrado ao cliente: não pode reaparecer na próxima busca."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Veja o que encontrei."), presented=["IMV-099"])

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-099"],
        )

    def test_a_resposta_tem_precedencia_sobre_os_codigos_apresentados(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Este aqui.", "IMV-001"), presented=["IMV-099"])

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(
            conversation_id=conversation.pk, property_codes=["IMV-001"],
        )

    def test_conversa_sem_imovel_nao_recomenda_nada(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.replies_with(reply_with("Em qual bairro você procura?"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(conversation_id=conversation.pk, property_codes=[])


class TestFallback:
    """Falha de IA não pode deixar o cliente no vácuo."""

    @pytest.mark.parametrize(
        "erro",
        [
            ModelBehaviorError("resposta incompleta"),
            MaxTurnsExceeded("excedeu o número de turnos"),
            RuntimeError("erro inesperado"),
        ],
        ids=["model_behavior", "max_turns", "inesperado"],
    )
    def test_responde_com_a_mensagem_de_fallback(
        self, runner: FakeRunner, erro: Exception,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(erro)

        run_task(conversation, trigger)

        assert Message.objects.get(role=MessageRole.ASSISTANT).content == FALLBACK_MESSAGE

    def test_a_task_nao_propaga_o_erro_para_o_celery(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        """Propagar faria o Celery reenfileirar e cobrar a IA de novo."""

        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("falhou"))

        run_task(conversation, trigger)  # não levanta

    def test_o_fallback_nao_recomenda_imovel_algum(
        self, runner: FakeRunner,
        make_conversation: MakeConversation, make_message: MakeMessage,
    ) -> None:
        conversation = make_conversation()
        trigger = make_message(conversation, NOW)
        runner.fails_with(ModelBehaviorError("falhou"))

        with mock.patch("conversations.tasks.add_recommendations") as add:
            run_task(conversation, trigger)

        add.assert_called_once_with(conversation_id=conversation.pk, property_codes=[])
