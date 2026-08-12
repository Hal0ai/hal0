"""Tests for the mutating ``hal0 slot`` verbs' HTTP timeout (#1832).

The server-side lifecycle handlers (load/unload/restart/swap) block the
HTTP response until the slot's state machine converges — up to 180s for
a cold load (``providers.container._HEALTH_TIMEOUT_S``), and longer still
for restart/swap, which unload then load. ``api_post``'s default read
timeout is 10s, an 18x mismatch: any lifecycle op over ~10s used to raise
``ReadTimeout`` and exit 1 while the operation continued and completed
successfully server-side. These call sites must pass an explicit timeout
with headroom over the server's worst-case budget.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        captured["kwargs"] = kw
        return {"state": "serving", "model_id": (json or {}).get("model_id")}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)
    return captured


def _timeout_ge_server_budget(kwargs: dict[str, Any]) -> None:
    """The CLI's client-side read timeout must clear the server's own
    180s health-poll budget with real headroom — not just barely exceed
    the old 10s default."""
    assert "timeout" in kwargs, "lifecycle call site passed no explicit timeout kwarg"
    assert kwargs["timeout"] >= 180.0, (
        f"timeout={kwargs['timeout']} is under the server's 180s health-poll budget"
    )


def test_slot_load_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["load", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/load"
    _timeout_ge_server_budget(captured["kwargs"])


def test_slot_unload_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["unload", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/unload"
    _timeout_ge_server_budget(captured["kwargs"])


def test_slot_restart_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["restart", "primary"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/restart"
    _timeout_ge_server_budget(captured["kwargs"])


def test_slot_swap_passes_lifecycle_timeout(captured: dict[str, Any]) -> None:
    result = runner.invoke(
        slot_commands.app, ["swap", "primary", "--model", "demo", "--no-persist"]
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/slots/primary/swap"
    _timeout_ge_server_budget(captured["kwargs"])
