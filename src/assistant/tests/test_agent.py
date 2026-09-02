"""Comportamento do agente ponta a ponta, com modelo roteirizado.

Aqui o agente é o de produção — mesmas tools, mesmo prompt, mesmo
``output_type``. Só o modelo é trocado por um roteiro determinístico, o que
permite afirmar duas coisas que o teste de tool sozinho não alcança: qual tool
o agente expôs/executou, e o que voltou para o modelo depois da execução.
"""

import json
from typing import Any, Callable

from agents import Agent
from agents.testing import ScriptedModel

from assistant import SYSTEM_PROMPT
from assistant.agent import get_agent
from assistant.schemas import AgentReply
from assistant.tools import AssistantDeps

from .conftest import AgentRun, PropertyORM, answers, calls_faq, calls_search

RunAgent = Callable[..., AgentRun]
MakeProperty = Callable[..., Any]


class TestConfiguracaoDoAgente:
    def test_expoe_exatamente_as_duas_tools_do_dominio(
        self, run_agent: RunAgent,
    ) -> None:
        run = run_agent(answers("Olá!"))

        assert [tool.name for tool in run.first_call.tools] == [
            "search_properties",
            "faq_properties",
        ]

    def test_usa_o_prompt_do_dominio_como_instrucao(self, run_agent: RunAgent) -> None:
        run = run_agent(answers("Olá!"))

        assert run.first_call.system_instructions == SYSTEM_PROMPT

    def test_exige_a_resposta_no_formato_agent_reply(
        self, run_agent: RunAgent,
    ) -> None:
        run = run_agent(answers("Olá!"))

        assert run.first_call.output_schema is not None
        assert isinstance(run.reply, AgentReply)

    def test_get_agent_devolve_uma_instancia_nova_a_cada_chamada(self) -> None:
        """Cada conversa roda com o seu próprio agente: nada de estado compartilhado."""

        model = ScriptedModel()

        import assistant.agent as agent_module
        from unittest import mock

        with mock.patch.object(agent_module, "build_model", return_value=model):
            first, second = get_agent(), get_agent()

        assert isinstance(first, Agent)
        assert first is not second


class TestRoteamentoDeTools:
    """A pergunta do cliente tem que cair na tool certa."""

    def test_pedido_de_imovel_vai_para_a_busca(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub(code="IMV-001"))

        run = run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers("Encontrei o IMV-001."),
            message="Quero alugar em Boa Viagem até 3000",
        )

        assert run.tools_called == ["search_properties"]

    def test_duvida_sobre_a_imobiliaria_vai_para_o_faq(
        self, run_agent: RunAgent, faq_file: Callable[..., Any],
    ) -> None:
        faq_file({"pergunta": "Quais documentos?", "resposta": "RG e CPF."})

        run = run_agent(
            calls_faq(),
            answers("Você precisa de RG e CPF."),
            message="Que documento preciso para alugar?",
        )

        assert run.tools_called == ["faq_properties"]

    def test_faq_devolve_a_base_para_o_modelo(
        self, run_agent: RunAgent, faq_file: Callable[..., Any],
    ) -> None:
        faq_file({"pergunta": "Quais documentos?", "resposta": "RG e CPF."})

        run = run_agent(
            calls_faq(),
            answers("Você precisa de RG e CPF."),
            message="Que documento preciso?",
        )

        entradas = run.tool_outputs[0]
        assert [entrada.answer for entrada in entradas] == ["RG e CPF."]

    def test_conversa_sem_pedido_nao_chama_tool_nenhuma(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(answers("Olá! Como posso ajudar?"), message="bom dia")

        assert run.tools_called == []
        assert orm.searched is False


class TestPedidoDeInformacaoFaltante:
    """Sem filtro obrigatório, o agente pergunta em vez de buscar."""

    def test_a_tool_devolve_ao_modelo_a_orientacao_de_perguntar(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(
            calls_search(transaction_type="aluguel"),
            answers("Em qual bairro você procura?"),
            message="Quero alugar um apartamento",
        )

        orientacao = run.tool_outputs[0].guidance
        assert "bairro" in orientacao
        assert "preço" in orientacao
        assert "Pergunte ao cliente" in orientacao

    def test_nenhum_imovel_e_recomendado_quando_falta_filtro(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub(code="IMV-001"))
        deps = AssistantDeps(conversation_id=1)

        run = run_agent(
            calls_search(transaction_type="aluguel"),
            answers("Em qual bairro você procura?"),
            message="Quero alugar um apartamento",
            deps=deps,
        )

        assert run.reply.recommended_properties == []
        assert deps.presented_codes == []

    def test_a_orientacao_chega_ao_modelo_na_rodada_seguinte(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        """A pergunta ao cliente nasce do retorno da tool, não de adivinhação."""

        run = run_agent(
            calls_search(neighborhood="Boa Viagem"),
            answers("É para alugar ou comprar? E qual faixa de preço?"),
            message="Quero algo em Boa Viagem",
        )

        segunda_rodada = json.dumps(run.model.calls[1].input, ensure_ascii=False)
        assert "tipo de transação" in segunda_rodada


class TestNaoRepeteImovelNaConversa:
    def test_os_imoveis_ja_recomendados_saem_da_query(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.already_recommended(1, 2)
        orm.returns(property_stub(code="IMV-003"))

        run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers("Tenho o IMV-003."),
            message="Me mostre outras opções",
        )

        assert orm.excluded_ids == [1, 2]

    def test_nova_busca_na_mesma_conversa_nao_repete_o_ja_apresentado(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.already_recommended(1)
        orm.returns(property_stub(code="IMV-002"))
        deps = AssistantDeps(conversation_id=1, presented_codes=["IMV-001"])

        run = run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers("Tenho também o IMV-002."),
            message="Tem mais alguma opção?",
            deps=deps,
        )

        apresentados = [imovel.code for imovel in run.tool_outputs[0].properties]
        assert "IMV-001" not in apresentados
        assert apresentados == ["IMV-002"]


class TestLimiteDeImoveisPorResposta:
    def test_o_modelo_recebe_no_maximo_dois_imoveis(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(6)])

        run = run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers("Encontrei duas opções."),
            message="Quero alugar em Boa Viagem até 3000",
        )

        assert len(run.tool_outputs[0].properties) == 2

    def test_a_conversa_registra_no_maximo_dois_codigos_por_busca(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(*[property_stub(code=f"IMV-{index}") for index in range(6)])
        deps = AssistantDeps(conversation_id=1)

        run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers("Encontrei duas opções."),
            message="Quero alugar em Boa Viagem até 3000",
            deps=deps,
        )

        assert deps.presented_codes == ["IMV-0", "IMV-1"]


class TestRespostaFinal:
    def test_a_resposta_vira_agent_reply_com_os_imoveis_recomendados(
        self, run_agent: RunAgent, orm: PropertyORM, property_stub: MakeProperty,
    ) -> None:
        orm.returns(property_stub(code="IMV-001"))

        run = run_agent(
            calls_search(
                transaction_type="aluguel",
                neighborhood="Boa Viagem",
                max_price=3000,
            ),
            answers(
                "Encontrei o IMV-001.",
                recommended=[
                    {
                        "code": "IMV-001",
                        "price": 2500,
                        "neighborhood": "Boa Viagem",
                        "bedrooms": 2,
                        "address": "Rua dos Navegantes, 150",
                        "description": "Apartamento com varanda",
                    }
                ],
            ),
            message="Quero alugar em Boa Viagem até 3000",
        )

        assert run.reply.message == "Encontrei o IMV-001."
        assert [imovel.code for imovel in run.reply.recommended_properties] == ["IMV-001"]

    def test_resposta_sem_imovel_traz_a_lista_vazia(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(answers("Olá! Como posso ajudar?"))

        assert run.reply.recommended_properties == []
