from decimal import Decimal
from typing import Any, Iterator
from unittest.mock import patch

import pytest
from pydantic_ai import models
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.usage import UsageLimits

from assistant.agent import build_model, get_agent
from assistant.schemas import AgentReply
from assistant.tools import MAX_SEARCHES_PER_RUN, AssistantDeps
from conversations.models import Conversation
from properties.enums import TransactionType
from properties.models import Property

# `transaction=True`: o pydantic-ai executa tools síncronas numa thread
# separada, que abre outra conexão e não enxergaria dados só na transação
# do teste.
pytestmark = pytest.mark.django_db(transaction=True)

PHONE = "+5581982860171"


@pytest.fixture(autouse=True)
def bloqueia_chamadas_reais() -> Iterator[None]:
    """Nenhum teste pode sair para a OpenAI: o SDK levanta erro se tentar."""
    with models.override_allow_model_requests(False):
        yield


@pytest.fixture
def conversation() -> Conversation:
    return Conversation.objects.create(user_phone=PHONE)


def make_property(code: str, **overrides: Any) -> Property:
    fields: dict[str, Any] = {
        "transaction_type": TransactionType.RENT,
        "neighborhood": "Boa Viagem",
        "price": Decimal("2500"),
        "bedrooms": 2,
        "address": f"Rua {code}",
        "description": f"Imóvel {code}",
    }
    fields.update(overrides)
    return Property.objects.create(code=code, **fields)


def scripted_model(*responses: ModelResponse) -> tuple[FunctionModel, list[list[Any]]]:
    """Modelo falso que devolve `responses` em ordem, guardando o que recebeu."""
    received: list[list[Any]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        received.append(list(messages[-1].parts))
        return responses[min(len(received) - 1, len(responses) - 1)]

    return FunctionModel(respond), received


def reply(message: str, properties: list[dict[str, Any]] | None = None) -> ToolCallPart:
    return ToolCallPart(
        "final_result",
        {"message": message, "recommended_properties": properties or []},
    )


def run(agent_model: FunctionModel, prompt: str, conversation: Conversation) -> Any:
    with patch("assistant.agent.build_model", return_value=agent_model):
        agent = get_agent()
    return agent.run_sync(
        prompt, deps=AssistantDeps(conversation_id=conversation.pk)
    )


# --- fiação do agente -------------------------------------------------------


def test_agente_recebe_as_deps_tipadas(conversation: Conversation) -> None:
    """Regressão: `deps_type=int` fazia a tool quebrar em `ctx.deps.conversation_id`."""
    make_property("IMV-001")
    model, _ = scripted_model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_properties",
                    {
                        "transaction_type": "aluguel",
                        "neighborhood": "Boa Viagem",
                        "max_price": 3000,
                    },
                )
            ]
        ),
        ModelResponse(parts=[reply("Encontrei uma opção.")]),
    )

    result = run(model, "quero alugar em Boa Viagem até 3000", conversation)

    assert isinstance(result.output, AgentReply)


def test_resultado_da_tool_chega_ao_modelo(conversation: Conversation) -> None:
    make_property("IMV-001")
    model, received = scripted_model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_properties",
                    {
                        "transaction_type": "aluguel",
                        "neighborhood": "Boa Viagem",
                        "max_price": 3000,
                    },
                )
            ]
        ),
        ModelResponse(parts=[reply("Encontrei o IMV-001.")]),
    )

    run(model, "quero alugar em Boa Viagem até 3000", conversation)

    tool_returns = [part for part in received[1] if isinstance(part, ToolReturnPart)]
    assert "IMV-001" in tool_returns[0].model_response_str()


def test_faq_esta_disponivel_para_o_agente(conversation: Conversation) -> None:
    model, received = scripted_model(
        ModelResponse(parts=[ToolCallPart("faq_properties", {})]),
        ModelResponse(parts=[reply("A taxa de corretagem é de um mês de aluguel.")]),
    )

    run(model, "qual a taxa de corretagem?", conversation)

    tool_returns = [part for part in received[1] if isinstance(part, ToolReturnPart)]
    assert "corretagem" in tool_returns[0].model_response_str()


def test_as_duas_tools_sao_expostas_ao_modelo(conversation: Conversation) -> None:
    exposed: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        exposed.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[reply("Olá!")])

    run(FunctionModel(respond), "oi", conversation)

    assert set(exposed) == {"search_properties", "faq_properties"}


# --- proteção contra loop ---------------------------------------------------


def test_busca_vazia_devolve_orientacao_em_vez_de_lista_vazia(
    conversation: Conversation,
) -> None:
    """Regressão: com `[]` puro o modelo variava filtros sozinho, uma request por tentativa."""
    model, received = scripted_model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_properties",
                    {
                        "transaction_type": "aluguel",
                        "neighborhood": "Bairro Inexistente",
                        "max_price": 3000,
                    },
                )
            ]
        ),
        ModelResponse(parts=[reply("Não encontrei imóveis com esses filtros.")]),
    )

    run(model, "quero alugar no Bairro Inexistente até 3000", conversation)

    tool_returns = [part for part in received[1] if isinstance(part, ToolReturnPart)]
    assert "repita a busca" in tool_returns[0].model_response_str()


def test_agente_teimoso_para_de_consultar_apos_o_teto_de_buscas(
    conversation: Conversation,
) -> None:
    """Mesmo insistindo, o modelo não consegue disparar mais buscas que o teto."""
    make_property("IMV-001")
    buscas = {"n": 0}

    def insiste(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        buscas["n"] += 1
        if buscas["n"] > MAX_SEARCHES_PER_RUN + 1:
            return ModelResponse(parts=[reply("Desisto.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_properties",
                    {
                        "transaction_type": "aluguel",
                        "neighborhood": "Boa Viagem",
                        "max_price": 3000 + buscas["n"],
                    },
                )
            ]
        )

    deps = AssistantDeps(conversation_id=conversation.pk)
    with patch("assistant.agent.build_model", return_value=FunctionModel(insiste)):
        agent = get_agent()
    agent.run_sync("quero alugar", deps=deps)

    assert deps.searches_done == MAX_SEARCHES_PER_RUN


def test_busca_sem_filtros_nao_devolve_imovel_e_orienta_o_modelo(
    conversation: Conversation,
) -> None:
    """A guarda determinística vira instrução, não exceção que derruba a task."""
    make_property("IMV-001")
    model, received = scripted_model(
        ModelResponse(parts=[ToolCallPart("search_properties", {})]),
        ModelResponse(parts=[reply("Para qual bairro você procura?")]),
    )

    result = run(model, "quero um imóvel", conversation)

    retorno = " ".join(str(part) for part in received[1])
    assert "bairro" in retorno
    assert result.output.recommended_properties == []


def test_limite_de_requisicoes_interrompe_o_agente_em_loop(
    conversation: Conversation,
) -> None:
    """Modelo que nunca conclui: o teto corta em vez de gastar chamadas à toa."""
    chamadas = {"n": 0}

    def nunca_conclui(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        chamadas["n"] += 1
        return ModelResponse(parts=[ToolCallPart("faq_properties", {})])

    with patch("assistant.agent.build_model", return_value=FunctionModel(nunca_conclui)):
        agent = get_agent()

    with pytest.raises(Exception, match="request_limit"):
        agent.run_sync(
            "oi",
            deps=AssistantDeps(conversation_id=conversation.pk),
            usage_limits=UsageLimits(request_limit=3),
        )

    assert chamadas["n"] == 3


def test_resposta_em_texto_puro_vira_reply_valida(conversation: Conversation) -> None:
    """Regressão: modelo que responde em texto estourava UnexpectedModelBehavior."""
    model, _ = scripted_model(
        ModelResponse(parts=[TextPart("Temos duas opções em Boa Viagem.")])
    )

    result = run(model, "quero alugar", conversation)

    assert result.output.message == "Temos duas opções em Boa Viagem."
    assert result.output.recommended_properties == []


def test_texto_com_json_do_schema_e_aproveitado(conversation: Conversation) -> None:
    """gpt-oss às vezes despeja o JSON como texto; o cliente não pode ver isso."""
    model, _ = scripted_model(
        ModelResponse(
            parts=[TextPart('{"message": "Oi!", "recommended_properties": []}')]
        )
    )

    result = run(model, "oi", conversation)

    assert result.output.message == "Oi!"


def test_texto_puro_depois_de_uma_busca_tambem_e_aceito(
    conversation: Conversation,
) -> None:
    make_property("IMV-001")
    model, _ = scripted_model(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_properties",
                    {
                        "transaction_type": "aluguel",
                        "neighborhood": "Boa Viagem",
                        "max_price": 3000,
                    },
                )
            ]
        ),
        ModelResponse(parts=[TextPart("Encontrei o IMV-001 por R$ 2.500.")]),
    )

    result = run(model, "quero alugar em Boa Viagem", conversation)

    assert "IMV-001" in result.output.message


# --- escolha do modelo ------------------------------------------------------


def test_em_debug_usa_o_modelo_local() -> None:
    """Regressão: sem base_url e api_key vindos do settings, a Ollama Cloud
    devolve connection error ou 401."""
    with patch("assistant.agent.settings.DEBUG", True), patch(
        "assistant.agent.settings.OLLAMA_BASE_URL", "https://ollama.com/v1"
    ), patch("assistant.agent.settings.OLLAMA_API_KEY", "chave-de-teste"), patch(
        "assistant.agent.settings.OLLAMA_MODEL", "gpt-oss:20b"
    ):
        model = build_model()

    assert isinstance(model, OllamaModel)
    assert model.model_name == "gpt-oss:20b"
    assert model.base_url == "https://ollama.com/v1/"
    assert model._provider.client.api_key == "chave-de-teste"


def test_fora_de_debug_usa_a_openai_direto() -> None:
    """Regressão: o gateway da Pydantic exige chave própria, não a da OpenAI."""
    with patch("assistant.agent.settings.DEBUG", False), patch(
        "assistant.agent.settings.OPENAI_API_KEY", "sk-teste"
    ):
        model = build_model()

    assert isinstance(model, OpenAIChatModel)
    assert model.base_url == "https://api.openai.com/v1/"
