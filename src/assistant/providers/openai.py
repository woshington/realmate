from assistant.providers.base import BaseAssistantClient
from django.conf import settings
from typing import Any


class OpenAIClient(BaseAssistantClient):
    def __init__(self, api_key: str | None = None, model: str  | None = None, timeout: float = 30.0):
        super().__init__(
            api_key=api_key or settings.OPENAI_API_KEY,
            model=model or settings.OPENAI_MODEL,
            timeout=timeout or settings.OPENAI_TIMEOUT
        )

    def chat(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        return {"provider": "openai", "model": self.model}