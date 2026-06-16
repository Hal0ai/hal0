from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


def test_slot_status_warns_on_config_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/slots/chat"
        return {
            "name": "chat",
            "status": "ready",
            "model_id": "qwen3-4b-q4_k_m",
            "port": 8081,
            "config_drift": {
                "drifted": True,
                "diffs": [
                    {"key": "--ctx-size", "running": "4096", "rendered": "131072"},
                    {"key": "-b", "running": "512", "rendered": "2048"},
                ],
            },
        }

    monkeypatch.setattr(slot_commands, "api_get", fake_get)

    result = runner.invoke(slot_commands.app, ["status", "chat"])

    assert result.exit_code == 0, result.output
    assert "WARN" in result.output
    assert "--ctx-size" in result.output
    assert "4096" in result.output
    assert "131072" in result.output
    assert "-b" in result.output
