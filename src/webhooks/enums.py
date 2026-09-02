from enum import StrEnum


class WebhookEvent(StrEnum):
    """Eventos entregues pelo provedor de mensageria no endpoint único.

    Só os eventos listados aqui têm tratamento. Qualquer outro valor é aceito
    com ``200 OK`` e descartado — o provedor não deve reenviar um evento que
    ainda não sabemos tratar.
    """

    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
