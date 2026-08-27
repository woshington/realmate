SEARCH_PROPERTIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_properties",
        "description": (
            "Busca imóveis disponíveis. Use o código do imóvel sozinho, OU a "
            "combinação de tipo de transação + bairro + ao menos um limite de "
            "preço. Se os campos obrigatórios não forem informados, a tool "
            "retorna quais estão faltando e você deve perguntar ao cliente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Código do imóvel, se o cliente informou.",
                },
                "transaction_type": {
                    "type": "string",
                    "enum": ["rent", "sale"],
                    "description": "Aluguel (rent) ou venda (sale).",
                },
                "neighborhood": {
                    "type": "string",
                    "description": "Bairro em Recife/PE.",
                },
                "min_price": {
                    "type": "number",
                    "description": "Preço mínimo (piso).",
                },
                "max_price": {
                    "type": "number",
                    "description": "Preço máximo (teto).",
                },
                "bedrooms": {
                    "type": "integer",
                    "description": "Quantidade de quartos (opcional).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

ANSWER_FAQ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "answer_faq",
        "description": (
            "Busca a resposta para uma dúvida institucional sobre a imobiliária "
            "(documentos, taxas, horários, procedimentos) na base de perguntas "
            "frequentes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Pergunta do cliente, em português.",
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}