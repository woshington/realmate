from assistant.providers.base import BaseAssistantClient
from config import settings
from typing import Any


class OllamaClient(BaseAssistantClient):
    def __init__(self, api_key: str | None = None, model: str  | None = None, timeout: float = 30.0):
        super().__init__(
            api_key=api_key,  # Ollama typically doesn't require API key
            model=model or getattr(settings, 'OLLAMA_MODEL', 'llama2'),
            timeout=timeout
        )
        self.base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')

    def chat(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        return {"provider": "ollama", "model": self.model, "base_url": self.base_url}
