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


# ── #2919: the engine's model can be an upstream model ───────────────────────


def test_enable_with_model_sends_extraction_model(stub_api) -> None:
    result = runner.invoke(memory_commands.graph_app, ["enable", "--model", "MiniMax-M2.5"])
    assert result.exit_code == 0, result.output
    assert stub_api["put"] == [
        ("/api/memory/graph", {"enabled": True, "extraction_model": "MiniMax-M2.5"})
    ]


def test_enable_local_clears_the_model(stub_api) -> None:
    result = runner.invoke(memory_commands.graph_app, ["enable", "--local"])
    assert result.exit_code == 0, result.output
    assert stub_api["put"] == [("/api/memory/graph", {"enabled": True, "extraction_model": None})]


@pytest.mark.parametrize(
    "argv", [["enable", "--model", "x", "--local"], ["enable", "--model", "x", "--slot", "agent"]]
)
def test_enable_model_is_exclusive_with_local_and_slot(stub_api, argv: list[str]) -> None:
    result = runner.invoke(memory_commands.graph_app, argv)
    assert result.exit_code != 0
    assert "exclusive" in result.output
    assert stub_api["put"] == []


def test_enable_panel_names_the_engine_target(stub_api, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_put(path: str, **kw: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "extraction_slot": "utility",
            "extraction_model": "MiniMax-M2.5",
            "target": {"kind": "upstream", "model": "MiniMax-M2.5", "alias": "hal0/memory"},
            "status": {"enabled": True},
        }

    monkeypatch.setattr(memory_commands, "api_put", fake_put)
    result = runner.invoke(memory_commands.graph_app, ["enable", "--model", "MiniMax-M2.5"])
    assert result.exit_code == 0, result.output
    assert "MiniMax-M2.5" in result.output
    assert "upstream model" in result.output


def test_status_shows_target_and_what_the_engine_really_runs(
    stub_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2918: an operator override in hindsight-llm.env is named, in red, with
    the keys that came from it — not hidden behind the configured slot."""

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "extraction_slot": "utility",
            "slot_resolves": True,
            "available_slots": ["agent", "utility"],
            "extraction_model": None,
            "target": {"kind": "slot", "model": "hal0/utility", "alias": "hal0/memory"},
            "available_models": [
                {"id": "MiniMax-M2.5", "owned_by": "minimax"},
                {"id": "minimax/minimax-m3", "owned_by": "openrouter"},
            ],
            "engine_llm": {
                "provider": "minimax",
                "base_url": "https://api.minimax.io/v1",
                "model": "MiniMax-M2.5",
                "overridden": True,
                "override_file": "/etc/hal0/hindsight-llm.env",
                "override_keys": ["HINDSIGHT_API_LLM_MODEL", "HINDSIGHT_API_LLM_PROVIDER"],
            },
            "in_flight": 0,
            "builds_ok": 1,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    monkeypatch.setattr(memory_commands, "api_get", fake_get)
    result = runner.invoke(memory_commands.graph_app, ["status"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Engine target" in out and "hal0/utility" in out and "local slot" in out
    assert "Upstream models" in out and "minimax/minimax-m3" in out
    assert "Engine running" in out and "via minimax" in out
    assert "overridden by /etc/hal0/hindsight-llm.env" in out
    assert "HINDSIGHT_API_LLM_PROVIDER" in out


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


# ── #2353: extraction throughput warning ─────────────────────────────────────


def _slow_estimate() -> dict[str, Any]:
    from hal0.memory.extraction_env import ExtractionThroughput

    return ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=12.0, decode_tps=4.0
    ).as_dict()


def test_enable_prints_the_throughput_warning(stub_api, monkeypatch: pytest.MonkeyPatch) -> None:
    est = _slow_estimate()
    seen: dict[str, Any] = {}

    def fake_put(path: str, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {
            "enabled": True,
            "extraction_slot": "utility",
            "throughput": est,
            "warnings": [est["message"]],
        }

    monkeypatch.setattr(memory_commands, "api_put", fake_put)
    result = runner.invoke(memory_commands.app, ["graph", "enable"], terminal_width=400)

    assert result.exit_code == 0, result.output  # a warning, never a refusal
    assert "Graph extraction enabled" in result.output
    assert "12.0 prompt tok/s" in result.output
    # Rendered literally, not swallowed as a rich markup tag.
    assert "[memory.graph].llm_timeout_s = 300 s" in result.output
    # The PUT may restart hindsight-api and then probe: the 10 s default is too short.
    assert seen["timeout"] > 60


def test_graph_status_shows_the_cached_estimate(stub_api, monkeypatch: pytest.MonkeyPatch) -> None:
    est = _slow_estimate()
    monkeypatch.setattr(
        memory_commands,
        "api_get",
        lambda path, **_kw: {"enabled": True, "extraction_slot": "utility", "throughput": est},
    )
    result = runner.invoke(memory_commands.app, ["graph", "status"], terminal_width=400)

    assert result.exit_code == 0, result.output
    assert "Extraction speed" in result.output
    assert "12.0 prompt tok/s" in result.output


def test_graph_check_posts_the_measurement_and_exits_zero_when_slow(
    stub_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    est = _slow_estimate()
    posts: list[tuple[str, dict[str, Any]]] = []

    def fake_post(path: str, **kw: Any) -> dict[str, Any]:
        posts.append((path, kw))
        return {"throughput": est}

    monkeypatch.setattr(memory_commands, "api_post", fake_post)
    result = runner.invoke(memory_commands.app, ["graph", "check"], terminal_width=400)

    assert result.exit_code == 0, result.output
    assert posts[0][0] == "/api/memory/graph/throughput"
    assert posts[0][1]["timeout"] > 30  # room for the 30 s probe cap
    assert "12.0 prompt tok/s" in result.output

    as_json = runner.invoke(memory_commands.app, ["graph", "check", "--json"])
    assert json.loads(as_json.output)["verdict"] == "too_slow"
