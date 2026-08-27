from enum import Enum


class EventEnum(Enum):
    MESSAGE_RECEIVED = "message_received"


class ResponseEnum(Enum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"