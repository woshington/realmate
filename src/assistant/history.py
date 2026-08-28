from collections.abc import Iterable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from conversations.enums import MessageRole
from conversations.models import Message


def to_model_messages(messages: Iterable[Message]) -> list[ModelMessage]:
    history: list[ModelMessage] = []

    for message in messages:
        if message.role == MessageRole.CUSTOMER:
            history.append(
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=message.content, timestamp=message.timestamp
                        )
                    ]
                )
            )
        else:
            history.append(
                ModelResponse(
                    parts=[TextPart(content=message.content)],
                    timestamp=message.timestamp,
                )
            )

    return history
