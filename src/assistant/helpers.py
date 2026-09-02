from collections.abc import Iterable

from agents import TResponseInputItem
from openai.types.responses import EasyInputMessageParam

from conversations.enums import MessageRole
from conversations.models import Message


def to_model_messages(
    messages: Iterable[Message],
) -> list[TResponseInputItem]:
    return [
        EasyInputMessageParam(
            role=(
                "user"
                if message.role == MessageRole.CUSTOMER
                else "assistant"
            ),
            content=message.content,
        )
        for message in messages
    ]