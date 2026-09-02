"""End-to-end agent behavior, driven by a scripted model.

The agent here is the production one — same tools, same prompt, same
``output_type``. Only the model is replaced by a deterministic script, which
allows asserting two things a tool test alone cannot reach: which tool the agent
exposed/executed, and what came back to the model after the execution.
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


class TestAgentConfiguration:
    def test_exposes_exactly_the_two_domain_tools(
        self, run_agent: RunAgent,
    ) -> None:
        run = run_agent(answers("Olá!"))

        assert [tool.name for tool in run.first_call.tools] == [
            "search_properties",
            "faq_properties",
        ]

    def test_uses_the_domain_prompt_as_instructions(self, run_agent: RunAgent) -> None:
        run = run_agent(answers("Olá!"))

        assert run.first_call.system_instructions == SYSTEM_PROMPT

    def test_requires_the_answer_in_the_agent_reply_format(
        self, run_agent: RunAgent,
    ) -> None:
        run = run_agent(answers("Olá!"))

        assert run.first_call.output_schema is not None
        assert isinstance(run.reply, AgentReply)

    def test_get_agent_returns_a_fresh_instance_on_every_call(self) -> None:
        """Every conversation runs with its own agent: no shared state."""

        model = ScriptedModel()

        import assistant.agent as agent_module
        from unittest import mock

        with mock.patch.object(agent_module, "build_model", return_value=model):
            first, second = get_agent(), get_agent()

        assert isinstance(first, Agent)
        assert first is not second


class TestToolRouting:
    """The customer's question has to land on the right tool."""

    def test_property_request_goes_to_the_search_tool(
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

    def test_question_about_the_agency_goes_to_the_faq(
        self, run_agent: RunAgent, faq_file: Callable[..., Any],
    ) -> None:
        faq_file({"pergunta": "Quais documentos?", "resposta": "RG e CPF."})

        run = run_agent(
            calls_faq(),
            answers("Você precisa de RG e CPF."),
            message="Que documento preciso para alugar?",
        )

        assert run.tools_called == ["faq_properties"]

    def test_faq_returns_the_knowledge_base_to_the_model(
        self, run_agent: RunAgent, faq_file: Callable[..., Any],
    ) -> None:
        faq_file({"pergunta": "Quais documentos?", "resposta": "RG e CPF."})

        run = run_agent(
            calls_faq(),
            answers("Você precisa de RG e CPF."),
            message="Que documento preciso?",
        )

        entries = run.tool_outputs[0]
        assert [entry.answer for entry in entries] == ["RG e CPF."]

    def test_small_talk_calls_no_tool_at_all(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(answers("Olá! Como posso ajudar?"), message="bom dia")

        assert run.tools_called == []
        assert orm.searched is False


class TestAskingForMissingInformation:
    """Without a required filter, the agent asks instead of searching."""

    def test_the_tool_returns_the_guidance_to_ask(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(
            calls_search(transaction_type="aluguel"),
            answers("Em qual bairro você procura?"),
            message="Quero alugar um apartamento",
        )

        guidance = run.tool_outputs[0].guidance
        assert "bairro" in guidance
        assert "preço" in guidance
        assert "Pergunte ao cliente" in guidance

    def test_no_property_is_recommended_when_a_filter_is_missing(
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

    def test_the_guidance_reaches_the_model_on_the_next_turn(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        """The question to the customer comes from the tool output, not guesswork."""

        run = run_agent(
            calls_search(neighborhood="Boa Viagem"),
            answers("É para alugar ou comprar? E qual faixa de preço?"),
            message="Quero algo em Boa Viagem",
        )

        second_turn = json.dumps(run.model.calls[1].input, ensure_ascii=False)
        assert "tipo de transação" in second_turn


class TestNoPropertyRepeatsInTheConversation:
    def test_already_recommended_properties_are_left_out_of_the_query(
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

    def test_a_new_search_in_the_same_conversation_does_not_repeat_what_was_shown(
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

        presented = [property_.code for property_ in run.tool_outputs[0].properties]
        assert "IMV-001" not in presented
        assert presented == ["IMV-002"]


class TestPropertyLimitPerAnswer:
    def test_the_model_receives_at_most_two_properties(
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

    def test_the_conversation_records_at_most_two_codes_per_search(
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


class TestFinalAnswer:
    def test_the_answer_becomes_an_agent_reply_with_the_recommended_properties(
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
        assert [
            property_.code for property_ in run.reply.recommended_properties
        ] == ["IMV-001"]

    def test_an_answer_without_properties_carries_an_empty_list(
        self, run_agent: RunAgent, orm: PropertyORM,
    ) -> None:
        run = run_agent(answers("Olá! Como posso ajudar?"))

        assert run.reply.recommended_properties == []
