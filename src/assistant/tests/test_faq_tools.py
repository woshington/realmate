from pathlib import Path
from unittest import mock
from typing import Any
import json
from assistant.tools import (
    _load_faq,
    faq_properties
)


class TestFaqProperties:
    def setup_method(self) -> None:
        _load_faq.cache_clear()

    def teardown_method(self) -> None:
        _load_faq.cache_clear()

    def test_returns_entries_parsed_from_the_faq_file(
        self, tmp_path: Path, settings: Any,
    ) -> None:
        faq_file = tmp_path / "faq.json"
        faq_file.write_text(
            json.dumps([{"pergunta": "Quais documentos preciso?", "resposta": "RG e CPF."}]),
            encoding="utf-8",
        )
        settings.FAQ_JSON_PATH = str(faq_file)

        result = faq_properties()

        assert len(result) == 1
        assert result[0].ask == "Quais documentos preciso?"
        assert result[0].answer == "RG e CPF."

    def test_reads_the_faq_file_only_once_thanks_to_caching(
        self, tmp_path: Path, settings: Any,
    ) -> None:
        faq_file = tmp_path / "faq.json"
        faq_file.write_text(json.dumps([{"pergunta": "P", "resposta": "R"}]), encoding="utf-8")
        settings.FAQ_JSON_PATH = str(faq_file)

        with mock.patch("builtins.open", wraps=open) as mock_open:
            faq_properties()
            faq_properties()

        assert mock_open.call_count == 1