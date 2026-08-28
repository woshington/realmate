from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model, get_user_agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.gateway import gateway_provider
from django.conf import settings

from assistant import SYSTEM_PROMPT
from assistant.schemas import AgentReply
from assistant.tools import search_properties


def build_model() -> Model:
    if settings.DEBUG:
        return OllamaModel('qwen3')

    provider = gateway_provider('openai', api_key=settings.OPENAI_API_KEY)
    return OpenAIChatModel('gpt-5.2', provider=provider)


def get_agent() -> Agent[str, AgentReply]:
    return Agent[str, AgentReply](
        model=build_model(),
        deps_type=str,
        tools=[search_properties],
        output_type=AgentReply,
        system_prompt=SYSTEM_PROMPT,
    )