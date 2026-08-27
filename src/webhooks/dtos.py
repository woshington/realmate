from typing import TypedDict

from webhooks.enums import ResponseEnum


class ResponseWebhookDTO(TypedDict):
    status: ResponseEnum
    message: str