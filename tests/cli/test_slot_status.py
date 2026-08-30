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


def test_slot_status_warns_on_specialty_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 2026-08-29 (#1946) seam 4: the degraded reason must reach the CLI
    too, beside its ``config_drift`` sibling on the same detail payload."""
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/slots/pf"
        return {
            "name": "pf",
            "status": "ready",
            "model_id": "qwen-pf",
            "port": 8099,
            "specialty_degraded": {
                "code": "slot.specialty_degraded",
                "specialty": "promptforge",
                "runner": "rocmfpx",
                "detail": "runner 'rocmfpx' does not list specialty 'promptforge'",
            },
        }

    monkeypatch.setattr(slot_commands, "api_get", fake_get)

    result = runner.invoke(slot_commands.app, ["status", "pf"])

    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())  # Rich wraps at the terminal width
    assert "WARN specialty degraded" in out
    assert "promptforge" in out
    assert "rocmfpx" in out


def test_slot_status_silent_when_not_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain slot (null/absent key) prints no specialty line."""
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(
        slot_commands,
        "api_get",
        lambda path, **_kw: {
            "name": "chat",
            "status": "ready",
            "model_id": "m",
            "port": 8081,
            "specialty_degraded": None,
        },
    )

    result = runner.invoke(slot_commands.app, ["status", "chat"])

    assert result.exit_code == 0, result.output
    assert "specialty" not in result.output.lower()
