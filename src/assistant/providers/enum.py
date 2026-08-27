from enum import Enum

class AIProvider(str, Enum):
    OPENAI = "openai"
    OLLAMA = "ollama"