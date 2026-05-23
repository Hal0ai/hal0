"""Tests for ``hal0 memory graph {status,enable,disable}`` (ADR-0014 / #258).

Mocks the API layer so the CLI runs offline. Pins:

  - ``status`` projects ``enabled / route / counters / upstream`` into
    the panel + JSON view.
  - ``enable --route=upstream`` requires --provider + --model client-side.
  - ``enable --route=primary`` sends the right payload (no upstream block).
  - ``disable`` sends ``{enabled: false}``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_commands

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    """Stub the shared API helpers so CLI runs offline."""

    def fake_unreachable(_url: str) -> bool:
        return False

    monkeypatch.setattr(memory_commands, "_api_unreachable", fake_unreachable)

    calls: dict[str, list[Any]] = {"get": [], "put": []}

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        calls["get"].append(path)
        # Default status response — tests can override via monkeypatch.
        return {
            "enabled": False,
            "route": "upstream",
            "upstream": None,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def fake_put(path: str, **kw: Any) -> dict[str, Any]:
        calls["put"].append((path, kw.get("json")))
        # Echo back the payload as if the server accepted.
        body = kw.get("json", {})
        return {**body, "status": {"enabled": body.get("enabled", False)}}

    monkeypatch.setattr(memory_commands, "api_get", fake_get)
    monkeypatch.setattr(memory_commands, "api_put", fake_put)
    return calls


def test_status_default_panel(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "status"])
    assert result.exit_code == 0, result.output
    # Off-by-default → "OFF" appears (rich may insert ANSI; just match
    # the substring).
    assert "OFF" in result.output
    assert "upstream" in result.output


def test_status_json(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["enabled"] is False
    assert payload["route"] == "upstream"


def test_enable_upstream_requires_provider_and_model(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app,
        ["graph", "enable", "--route", "upstream"],
    )
    assert result.exit_code != 0
    assert "--provider" in result.output


def test_enable_primary_sends_no_upstream(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app,
        ["graph", "enable", "--route", "primary"],
    )
    assert result.exit_code == 0, result.output
    assert stub_api["put"], "PUT should have been sent"
    _, payload = stub_api["put"][-1]
    assert payload == {"enabled": True, "route": "primary"}


def test_enable_upstream_with_provider_and_model(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app,
        [
            "graph",
            "enable",
            "--route",
            "upstream",
            "--provider",
            "openrouter",
            "--model",
            "anthropic/claude-3.5-sonnet",
        ],
    )
    assert result.exit_code == 0, result.output
    _, payload = stub_api["put"][-1]
    assert payload["enabled"] is True
    assert payload["upstream"] == {
        "provider": "openrouter",
        "model": "anthropic/claude-3.5-sonnet",
    }


def test_enable_invalid_route_rejected_client_side(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app,
        ["graph", "enable", "--route", "bogus"],
    )
    assert result.exit_code != 0
    # The CLI rejects before sending — no PUT should land.
    assert stub_api["put"] == []


def test_disable_sends_enabled_false(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "disable"])
    assert result.exit_code == 0, result.output
    _, payload = stub_api["put"][-1]
    assert payload == {"enabled": False}


def test_disable_json(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "disable", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["enabled"] is False
