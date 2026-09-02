from pydantic import BaseModel, Field
from typing import Literal


class AssistantDeps(BaseModel):
    conversation_id: int
    presented_codes: list[str] = Field(default_factory=list)
    searches_done: int = 0

TransactionTypeFilter = Literal["aluguel", "venda"]

SEARCH_BUDGET_SPENT = (
    "Limite de buscas para esta mensagem atingido. Responda ao cliente com o "
    "que já foi encontrado e peça novos filtros. NÃO busque de novo agora."
)

NOTHING_FOUND = (
    "Nenhum imóvel encontrado com esses filtros. Diga isso ao cliente e "
    "pergunte se ele quer ajustar bairro, faixa de preço ou quartos. NÃO "
    "repita a busca com filtros que o cliente não informou."
)

FOUND = (
    "Apresente estes imóveis ao cliente: código, bairro, preço, quartos e um "
    "resumo da descrição."
)
