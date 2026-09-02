from functools import lru_cache
from agents import function_tool
from assistant.schemas import FaqEntry
from django.conf import settings
import json


@function_tool
def faq_properties() -> list[FaqEntry]:
    """
    Consulta a base de perguntas frequentes sobre a imobiliária Realmate.

    Use esta ferramenta para dúvidas sobre a Realmate, como documentos necessários,
    taxas, prazos, horários de atendimento, formas de pagamento, visitas e contratos.

    A ferramenta retorna todas as perguntas e respostas disponíveis na base.

    Responda ao cliente usando APENAS as informações presentes nas entradas retornadas.
    Nunca deduza, invente ou complemente informações que não estejam na base.

    Caso a base não contenha informações suficientes para responder à dúvida do cliente,
    informe que não encontrou a resposta para a dúvida e sugira que ele entre em contato
    diretamente com a imobiliária.

    Returns:
        Todas as entradas da base, cada uma contendo `pergunta` e `resposta`.
    """
    return list(_load_faq())


@lru_cache(maxsize=1)
def _load_faq() -> tuple[FaqEntry, ...]:
    with open(
        settings.FAQ_JSON_PATH,
        encoding="utf-8",
    ) as file:
        raw = json.load(file)

    return tuple(
        FaqEntry.model_validate(entry)
        for entry in raw
    )