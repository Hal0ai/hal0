"""Tests for ``hal0 memory graph {status,enable,disable}`` (ADR-0023 / #258).

Mocks the API layer so the CLI runs offline. Pins:

  - ``status`` projects ``enabled / extraction_slot / available_slots /
    counters`` into the panel + JSON view.
  - ``enable --slot <name>`` sends ``{enabled: true, extraction_slot: <name>}``.
  - ``enable`` with no ``--slot`` sends ``{enabled: true}`` (keep current slot).
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
            "extraction_slot": "utility",
            "slot_resolves": True,
            "available_slots": ["agent", "utility"],
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
        return {
            "enabled": body.get("enabled", False),
            "extraction_slot": body.get("extraction_slot", "utility"),
            "status": {"enabled": body.get("enabled", False)},
        }

    monkeypatch.setattr(memory_commands, "api_get", fake_get)
    monkeypatch.setattr(memory_commands, "api_put", fake_put)
    return calls


def test_status_default_panel(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "status"])
    assert result.exit_code == 0, result.output
    # Off-by-default → "OFF" appears (rich may insert ANSI; just match
    # the substring).
    assert "OFF" in result.output
    # The extraction slot + the available-slot list are surfaced.
    assert "Extraction slot" in result.output
    assert "utility" in result.output


def test_status_json(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["enabled"] is False
    assert payload["extraction_slot"] == "utility"


def test_enable_without_slot_keeps_current(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["graph", "enable"])
    assert result.exit_code == 0, result.output
    assert stub_api["put"], "PUT should have been sent"
    _, payload = stub_api["put"][-1]
    assert payload == {"enabled": True}


def test_enable_with_slot_sends_extraction_slot(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app,
        ["graph", "enable", "--slot", "agent"],
    )
    assert result.exit_code == 0, result.output
    _, payload = stub_api["put"][-1]
    assert payload == {"enabled": True, "extraction_slot": "agent"}


def test_route_option_no_longer_exists(stub_api) -> None:
    """ADR-0023: --route/--provider/--model were removed in favour of --slot."""
    result = runner.invoke(
        memory_commands.app,
        ["graph", "enable", "--route", "upstream"],
    )
    assert result.exit_code != 0
    # Typer surfaces the unknown option; no PUT should land.
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
