from django.conf import settings
from openai import AsyncOpenAI

from agents import Agent, OpenAIProvider

from assistant import SYSTEM_PROMPT
from assistant.schemas import AgentReply
from assistant.tools import (
    AssistantDeps,
)
from assistant.tools.search_properties_tools import search_properties
from assistant.tools.faq_tools import faq_properties


def build_model():
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    provider = OpenAIProvider(openai_client=client)

    return provider.get_model("gpt-5.2")


def get_agent() -> Agent[AssistantDeps]:
    return Agent[AssistantDeps](
        name="Property Assistant",
        model=build_model(),
        tools=[
            search_properties,
            faq_properties,
        ],
        output_type=AgentReply,
        instructions=SYSTEM_PROMPT,
    )