import json
from typing import Any, Callable
from unittest import mock
from unittest.mock import MagicMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from assistant.agent import build_model, reply_from_text
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps, faq_properties, search_properties


class TestReplyFromText:
    def test_valid_json_returns_parsed_agent_reply(self) -> None:
        text = json.dumps({"message": "Olá, como posso ajudar?"})

        result = reply_from_text(text)

        assert isinstance(result, AgentReply)
        assert result.message == "Olá, como posso ajudar?"

    def test_plain_text_is_wrapped_in_agent_reply(self) -> None:
        result = reply_from_text("Bom dia!")

        assert isinstance(result, AgentReply)
        assert result.message == "Bom dia!"
        assert result.recommended_properties == []


class TestBuildModel:
    def test_use_ollama_true_returns_ollama_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("assistant.agent.settings.USE_OLLAMA", True)
        monkeypatch.setattr("assistant.agent.settings.OLLAMA_MODEL", "llama3.1")
        monkeypatch.setattr(
            "assistant.agent.settings.OLLAMA_BASE_URL", "http://localhost:11434"
        )
        monkeypatch.setattr("assistant.agent.settings.OLLAMA_API_KEY", "fake-key")

        with mock.patch("assistant.agent.OllamaProvider") as mock_provider_cls, \
                mock.patch("assistant.agent.OllamaModel") as mock_model_cls:
            build_model()

        mock_provider_cls.assert_called_once_with(
            base_url="http://localhost:11434", api_key="fake-key",
        )
        mock_model_cls.assert_called_once_with(
            "llama3.1", provider=mock_provider_cls.return_value,
        )

    def test_use_ollama_false_returns_openai_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("assistant.agent.settings.USE_OLLAMA", False)
        monkeypatch.setattr("assistant.agent.settings.OPENAI_API_KEY", "sk-fake")

        with mock.patch("assistant.agent.OpenAIProvider") as mock_provider_cls, \
                mock.patch("assistant.agent.OpenAIChatModel") as mock_model_cls:
            build_model()

        mock_provider_cls.assert_called_once_with(api_key="sk-fake")
        mock_model_cls.assert_called_once_with(
            "gpt-5.2", provider=mock_provider_cls.return_value,
        )


class TestAgentRouting:
    def test_only_search_and_faq_tools_are_registered(
        self, agent: Agent[Any, Any],
    ) -> None:
        assert registered_tools_by_name(agent) == {
            "search_properties": search_properties,
            "faq_properties": faq_properties,
        }

    def test_search_properties_tool_is_called_for_property_search(
        self,
        agent: Agent[Any, Any],
        mock_property: MagicMock,
        mock_recommendation: MagicMock,
    ) -> None:
        route_by_keyword = model_that_routes_on_keyword(
            keyword="apartamento",
            tool_call_if_matched=ToolCallPart(
                tool_name="search_properties",
                args={
                    "transaction_type": "aluguel",
                    "neighborhood": "Boa Viagem",
                    "min_price": 1000,
                },
            ),
            tool_call_if_not_matched=ToolCallPart(tool_name="faq_properties", args={}),
        )

        with agent.override(model=FunctionModel(route_by_keyword)):
            result = agent.run_sync(
                "Quero um apartamento para alugar em Boa Viagem",
                deps=AssistantDeps(conversation_id=1),
            )

        assert tool_names_called_during(result) == ["search_properties"]
        mock_property.objects.filter.assert_called_once_with(
            transaction_type="aluguel",
            neighborhood__iexact="Boa Viagem",
            price__gte=1000,
        )

    def test_faq_properties_tool_is_called_for_faq_questions(
        self, agent: Agent[Any, Any], mock_faq: MagicMock,
    ) -> None:
        route_by_keyword = model_that_routes_on_keyword(
            keyword="documento",
            tool_call_if_matched=ToolCallPart(tool_name="faq_properties", args={}),
            tool_call_if_not_matched=ToolCallPart(
                tool_name="search_properties", args={"code": "IMV-001"},
            ),
        )

        with agent.override(model=FunctionModel(route_by_keyword)):
            result = agent.run_sync(
                "Quais documentos preciso para alugar?",
                deps=AssistantDeps(conversation_id=1),
            )

        assert tool_names_called_during(result) == ["faq_properties"]
        mock_faq.assert_called_once()


# ---- Helpers usados só nos testes acima ----------------------------------

def registered_tools_by_name(agent: Agent[Any, Any]) -> dict[str, Any]:
    return {
        name: tool.function
        for toolset in agent.toolsets
        for name, tool in getattr(toolset, "tools", {}).items()
    }


def model_that_routes_on_keyword(
    keyword: str,
    tool_call_if_matched: ToolCallPart,
    tool_call_if_not_matched: ToolCallPart,
) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    def fake_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        already_called_a_tool = any(
            isinstance(part, ToolCallPart) for m in messages for part in m.parts
        )
        if already_called_a_tool:
            return ModelResponse(parts=[TextPart("Pronto!")])

        user_message = first_user_prompt_in(messages).lower()
        chosen_tool_call = (
            tool_call_if_matched if keyword in user_message else tool_call_if_not_matched
        )
        return ModelResponse(parts=[chosen_tool_call])

    return fake_model


def first_user_prompt_in(messages: list[ModelMessage]) -> str:
    return next(
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )


def tool_names_called_during(result: AgentRunResult[Any]) -> list[str]:
    return [
        part.tool_name
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]