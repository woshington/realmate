"""Tool de perguntas frequentes.

A base de FAQ é a única fonte de verdade sobre a imobiliária. A tool devolve a
base inteira e o prompt proíbe complementar — por isso o que importa testar é
que ela lê o arquivo configurado, respeita o alias em português e não relê o
arquivo a cada pergunta.
"""

from pathlib import Path
from typing import Any, Callable
from unittest import mock

from assistant.tools.faq_tools import faq_properties

CallTool = Callable[..., Any]
WriteFaq = Callable[..., Path]


def ask_faq(call_tool: CallTool) -> Any:
    return call_tool(faq_properties)


class TestLeituraDaBase:
    def test_devolve_as_entradas_do_arquivo_configurado(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        faq_file(
            {"pergunta": "Quais documentos preciso?", "resposta": "RG e CPF."},
            {"pergunta": "Qual o horário?", "resposta": "Das 9h às 18h."},
        )

        entradas = ask_faq(call_tool)

        assert [entrada.ask for entrada in entradas] == [
            "Quais documentos preciso?",
            "Qual o horário?",
        ]
        assert [entrada.answer for entrada in entradas] == ["RG e CPF.", "Das 9h às 18h."]

    def test_traduz_o_alias_em_portugues_do_arquivo(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        """No disco é ``pergunta``/``resposta``; no domínio, ``ask``/``answer``."""

        faq_file({"pergunta": "Tem taxa?", "resposta": "Não cobramos taxa."})

        entrada = ask_faq(call_tool)[0]

        assert entrada.ask == "Tem taxa?"
        assert entrada.answer == "Não cobramos taxa."

    def test_base_vazia_devolve_lista_vazia(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        faq_file()

        assert ask_faq(call_tool) == []

    def test_a_base_real_do_projeto_carrega(self, call_tool: CallTool) -> None:
        """Fumaça sobre o arquivo versionado em ``data/``."""

        entradas = ask_faq(call_tool)

        assert entradas
        assert all(entrada.ask and entrada.answer for entrada in entradas)


class TestCacheDaBase:
    def test_le_o_arquivo_uma_unica_vez(
        self, call_tool: CallTool, faq_file: WriteFaq,
    ) -> None:
        """Uma pergunta de cliente não pode custar uma leitura de disco."""

        faq_file({"pergunta": "P", "resposta": "R"})

        with mock.patch("builtins.open", wraps=open) as spy_open:
            ask_faq(call_tool)
            ask_faq(call_tool)

        assert spy_open.call_count == 1


class TestArquivoInvalido:
    def test_arquivo_ausente_vira_erro_para_o_modelo_em_vez_de_derrubar_o_run(
        self, call_tool: CallTool, settings: Any, tmp_path: Path,
    ) -> None:
        """``@function_tool`` captura a exceção e devolve o erro como texto.

        Vale testar porque é o que separa "a IA responde que não conseguiu
        consultar" de "a conversa inteira cai no fallback".
        """

        settings.FAQ_JSON_PATH = str(tmp_path / "nao-existe.json")

        resultado = ask_faq(call_tool)

        assert isinstance(resultado, str)
        assert "error" in resultado.lower()
