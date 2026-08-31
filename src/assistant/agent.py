from pydantic import ValidationError
from pydantic_ai import Agent, TextOutput, ToolOutput
from pydantic_ai.models import Model
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.ollama import OllamaProvider
from django.conf import settings

from assistant import SYSTEM_PROMPT
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps, search_properties, faq_properties


def build_model() -> Model:
    if settings.DEBUG:
        return OllamaModel(
            settings.OLLAMA_MODEL,
            provider=OllamaProvider(
                base_url=settings.OLLAMA_BASE_URL,
                api_key=settings.OLLAMA_API_KEY,
            ),
        )

    provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)
    return OpenAIChatModel('gpt-5.2', provider=provider)


def reply_from_text(text: str) -> AgentReply:
    try:
        return AgentReply.model_validate_json(text)
    except ValidationError:
        return AgentReply(message=text)


def get_agent() -> Agent[AssistantDeps, AgentReply]:
    return Agent[AssistantDeps, AgentReply](
        model=build_model(),
        deps_type=AssistantDeps,
        tools=[search_properties, faq_properties],
        output_type=[ToolOutput(AgentReply), TextOutput(reply_from_text)],
        system_prompt=SYSTEM_PROMPT
    )
