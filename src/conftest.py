"""Configuração compartilhada da suíte.

Duas coisas moram aqui:

1. O isolamento de infraestrutura — cache e tracing — que nenhum teste deve
   precisar montar por conta própria.
2. As fábricas de objetos de domínio (``make_conversation``, ``make_message``,
   ``make_property``), usadas por mais de um app. Deixá-las aqui evita que cada
   pacote de testes reinvente o mesmo ``objects.create``.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator

import pytest
from agents import set_tracing_disabled
from django.core.cache import cache

from conversations.enums import MessageRole
from conversations.models import Conversation, Message
from properties.enums import TransactionType
from properties.models import Property

PHONE = "+5581982860171"
OTHER_PHONE = "+5581999998888"
NOW = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True, scope="session")
def disable_agent_tracing() -> Iterator[None]:
    """Nenhum teste pode falar com a OpenAI.

    O SDK sobe um exportador de traces em thread de fundo que faz POST em
    ``/v1/traces/ingest`` a cada run do agente. Desligado aqui, a suíte roda
    offline e não depende de credencial válida.
    """

    set_tracing_disabled(True)
    yield


@pytest.fixture(autouse=True)
def local_cache(settings: Any) -> None:
    """Cache em memória, limpo a cada teste.

    Em produção o cache é Redis (ver ``config.settings.CACHES``) e guarda o lock
    de processamento da conversa. Teste unitário não deve depender de um serviço
    de pé nem sujar a base de cache de quem está desenvolvendo.

    Trocar ``settings.CACHES`` dispara o sinal ``setting_changed``, que faz o
    Django reconstruir o handler — por isso a troca vale inclusive para quem já
    importou ``django.core.cache.cache``.
    """

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "realmate-tests",
        }
    }
    cache.clear()


@pytest.fixture
def make_conversation() -> Callable[..., Conversation]:
    def _make(
        user_phone: str = PHONE,
        last_message_at: datetime | None = None,
    ) -> Conversation:
        return Conversation.objects.create(
            user_phone=user_phone,
            last_message_at=last_message_at,
        )

    return _make


@pytest.fixture
def make_message() -> Callable[..., Message]:
    def _make(
        conversation: Conversation,
        timestamp: datetime = NOW,
        role: str = MessageRole.CUSTOMER,
        content: str = "Olá",
    ) -> Message:
        return Message.objects.create(
            external_id=uuid.uuid4(),
            conversation=conversation,
            content=content,
            role=role,
            timestamp=timestamp,
        )

    return _make


@pytest.fixture
def make_property() -> Callable[..., Property]:
    def _make(
        code: str = "IMV-001",
        transaction_type: str = TransactionType.RENT,
        neighborhood: str = "Boa Viagem",
        price: str = "2500.00",
        bedrooms: int = 2,
        address: str = "Rua dos Navegantes, 150",
        description: str = "Apartamento com varanda",
    ) -> Property:
        return Property.objects.create(
            code=code,
            transaction_type=transaction_type,
            neighborhood=neighborhood,
            price=Decimal(price),
            bedrooms=bedrooms,
            address=address,
            description=description,
        )

    return _make
