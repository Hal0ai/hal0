"""``hal0 model list`` marks the default model in its Rich table (#1796).

Before this fix, promoting a model with ``hal0 model default <id>`` was
only visible via ``model show``/the API — the list table had no column at
all reflecting which model was default.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import model_commands

runner = CliRunner()


def _stub_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_commands, "_api_unreachable", lambda _url: False)


def test_default_model_marked_in_table(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "models": [
            {"id": "qwen3-4b", "name": "Qwen3 4B", "default": True},
            {"id": "llama-8b", "name": "Llama 8B", "default": False},
        ]
    }
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(model_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(model_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    default_line = next(line for line in lines if "qwen3-4b" in line)
    other_line = next(line for line in lines if "llama-8b" in line)
    assert "*" in default_line
    assert "*" not in other_line


def test_no_default_model_marks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload: dict[str, Any] = {"models": [{"id": "qwen3-4b", "name": "Qwen3 4B"}]}
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(model_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(model_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "*" not in result.output
