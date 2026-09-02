"""Frequently asked questions tool.

The FAQ file is the single source of truth about the agency. The tool returns
the whole knowledge base and the prompt forbids adding to it — so what matters
to test is that it reads the configured file, honors the Portuguese aliases and
does not re-read the file on every question.
"""

from pathlib import Path
from typing import Any, Callable
from unittest import mock

from assistant.tools.faq_tools import faq_properties

CallTool = Callable[..., Any]
WriteFaq = Callable[..., Path]


def ask_faq(call_tool: CallTool) -> Any:
    return call_tool(faq_properties)


class TestReadingTheKnowledgeBase:
    def test_returns_the_entries_of_the_configured_file(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        faq_file(
            {"pergunta": "Quais documentos preciso?", "resposta": "RG e CPF."},
            {"pergunta": "Qual o horário?", "resposta": "Das 9h às 18h."},
        )

        entries = ask_faq(call_tool)

        assert [entry.ask for entry in entries] == [
            "Quais documentos preciso?",
            "Qual o horário?",
        ]
        assert [entry.answer for entry in entries] == ["RG e CPF.", "Das 9h às 18h."]

    def test_translates_the_portuguese_alias_from_the_file(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        """On disk it is ``pergunta``/``resposta``; in the domain, ``ask``/``answer``."""

        faq_file({"pergunta": "Tem taxa?", "resposta": "Não cobramos taxa."})

        entry = ask_faq(call_tool)[0]

        assert entry.ask == "Tem taxa?"
        assert entry.answer == "Não cobramos taxa."

    def test_an_empty_knowledge_base_returns_an_empty_list(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        faq_file()

        assert ask_faq(call_tool) == []

    def test_the_real_project_knowledge_base_loads(self, call_tool: CallTool) -> None:
        """Smoke test over the file versioned in ``data/``."""

        entries = ask_faq(call_tool)

        assert entries
        assert all(entry.ask and entry.answer for entry in entries)


class TestKnowledgeBaseCache:
    def test_reads_the_file_only_once(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        """A customer question must not cost a disk read."""

        faq_file({"pergunta": "P", "resposta": "R"})

        with mock.patch("builtins.open", wraps=open) as spy_open:
            ask_faq(call_tool)
            ask_faq(call_tool)

        assert spy_open.call_count == 1


class TestInvalidFile:
    def test_a_missing_file_becomes_an_error_for_the_model_instead_of_killing_the_run(
        self, call_tool: CallTool, settings: Any, tmp_path: Path,
    ) -> None:
        """``@function_tool`` catches the exception and returns the error as text.

        Worth testing because it is what separates "the AI answers that it could
        not look it up" from "the whole conversation falls back".
        """

        settings.FAQ_JSON_PATH = str(tmp_path / "does-not-exist.json")

        result = ask_faq(call_tool)

        assert isinstance(result, str)
        assert "error" in result.lower()
