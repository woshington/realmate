"""Test tooling for the property loading pipeline.

The real sources are files; the tests write temporary files in the same format.
``csv_row`` and ``json_record`` produce a valid record by default, and each test
overrides only the field it is exercising. Their keyword arguments keep the
source column names, since those are what the importers read.
"""

import json
from pathlib import Path
from typing import Any, Callable

import pytest

CSV_HEADER = "tipo_negocio,preco,quartos,bairro,endereco,descricao\n"


@pytest.fixture
def csv_row() -> Callable[..., str]:
    def _row(
        tipo: str = "aluguel",
        preco: str = "2500",
        quartos: str = "2",
        bairro: str = "Boa Viagem",
        endereco: str = "Rua dos Navegantes, 150",
        descricao: str = "Apartamento com varanda. codigo:IMV-001",
    ) -> str:
        return f'{tipo},{preco},{quartos},{bairro},"{endereco}","{descricao}"\n'

    return _row


@pytest.fixture
def write_csv(tmp_path: Path) -> Callable[..., Path]:
    def _write(*rows: str, header: str = CSV_HEADER, name: str = "imoveis.csv") -> Path:
        file_path = tmp_path / name
        file_path.write_text(header + "".join(rows), encoding="utf-8")
        return file_path

    return _write


@pytest.fixture
def json_record() -> Callable[..., dict[str, Any]]:
    def _record(**overrides: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "tipo_negocio": "aluguel",
            "preco": 2200,
            "quartos": 2,
            "bairro": "Espinheiro",
            "endereco": "Rua do Espinheiro, 340",
            "descricao": "Apartamento com 2 quartos. ref: C011",
        }
        record.update(overrides)
        return record

    return _record


@pytest.fixture
def write_json(tmp_path: Path) -> Callable[..., Path]:
    def _write(payload: Any, name: str = "imoveis.json") -> Path:
        file_path = tmp_path / name
        file_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )
        return file_path

    return _write
