from typing import Optional
from django.conf import settings

from assistant.providers.base import BaseAssistantClient
from assistant.providers.enum import AIProvider
from assistant.providers.ollama import OllamaClient
from assistant.providers.openai import OpenAIClient


class AssistantClientBuilder:
    @staticmethod
    def build(
        provider: Optional[AIProvider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> BaseAssistantClient:
        if provider is None:
            provider_str = getattr(settings, 'AI_PROVIDER', 'openai')
            provider = AIProvider(provider_str)

        client_map = {
            AIProvider.OPENAI: OpenAIClient,
            AIProvider.OLLAMA: OllamaClient,
        }

        client_class = client_map.get(provider)
        if client_class is None:
            raise ValueError(f"Unsupported AI provider: {provider}")

        kwargs = {}
        if api_key is not None:
            kwargs['api_key'] = api_key
        if model is not None:
            kwargs['model'] = model
        if timeout is not None:
            kwargs['timeout'] = timeout

        return client_class(**kwargs)


def get_assistant_client(**kwargs) -> BaseAssistantClient:
    return AssistantClientBuilder.build(**kwargs)
