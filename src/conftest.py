"""Configuração compartilhada da suíte.

O cache passou a ser Redis (ver ``config.settings.CACHES``), mas teste unitário
não deve depender de um serviço de pé nem sujar a base de cache de quem está
desenvolvendo. Aqui ele volta a ser local e é isolado por teste.
"""

from typing import Any

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def local_cache(settings: Any) -> None:
    """Cache em memória, limpo a cada teste.

    Trocar ``settings.CACHES`` dispara o sinal ``setting_changed``, que faz o
    Django reconstruir o handler de cache — por isso a troca vale para quem já
    importou ``django.core.cache.cache``.
    """

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "realmate-tests",
        }
    }
    cache.clear()
