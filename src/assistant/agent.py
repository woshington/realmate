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


def get_client():
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

def build_model():
    provider = OpenAIProvider(openai_client=get_client())

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

async def create_conversation() -> str | None:
    """Abre uma conversa no provider.

    O ``async with`` não é estilo: quem chama esta corrotina é um
    ``asyncio.run`` dentro da task Celery, que fecha o event loop ao terminar.
    Sem fechar o client aqui dentro, o ``httpx`` só solta a conexão quando o
    coletor de lixo passa — já com o loop fechado — e o `aclose` estoura em
    ``RuntimeError: Event loop is closed``, ruído que não derruba nada mas
    polui o log de toda conversa nova.
    """

    async with get_client() as client:
        response = await client.conversations.create()
        return response.id