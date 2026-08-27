from abc import ABC, abstractmethod
from typing import Any

class BaseAssistantClient(ABC):

    def __init__(self, api_key: str  | None = None, model: str  | None = None, timeout: float = 30.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        raise NotImplementedError

