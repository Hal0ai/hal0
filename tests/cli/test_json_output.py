"""Tests for ``--json`` machine-readable output on the list/show commands (#502).

`slot list`, `model list`, `agent list`, `slot show`, and `model show` each
grew a ``--json`` flag that emits the raw API response (the same payload the
command already fetches) as ``json.dumps(..., indent=2)`` and returns,
bypassing the Rich table/panel. Three hardware docs pipe
``hal0 slot list --json | grep`` — this is the documented-but-missing feature.

Each test stubs the shared API helpers so the CLI runs offline, then asserts
``json.loads(result.output)`` round-trips to the stubbed payload.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands, model_commands, slot_commands

runner = CliRunner()


def _stub_reachable(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    monkeypatch.setattr(module, "_api_unreachable", lambda _url: False)


# ── slot list --json ─────────────────────────────────────────────────────────


def test_slot_list_json_emits_raw_list(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"name": "primary", "status": "ready", "model": "qwen3-4b", "port": 8081},
        {"name": "embed", "status": "offline", "model": "bge-m3", "port": 8086},
    ]
    _stub_reachable(monkeypatch, slot_commands)
    monkeypatch.setattr(slot_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(slot_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == payload


def test_slot_list_json_empty_is_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_reachable(monkeypatch, slot_commands)
    monkeypatch.setattr(slot_commands, "api_get", lambda _p, **_k: [])

    result = runner.invoke(slot_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    # Empty must still be parseable JSON (the "No slots configured" Rich
    # line would NOT parse), so grep pipelines don't choke.
    assert json.loads(result.output) == []


def test_slot_list_json_no_rich_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"name": "primary", "status": "ready"}]
    _stub_reachable(monkeypatch, slot_commands)
    monkeypatch.setattr(slot_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(slot_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    # No Rich table chrome should leak into JSON output.
    assert "hal0 slots" not in result.output
    assert "┃" not in result.output


# ── model list --json ────────────────────────────────────────────────────────


def test_model_list_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"models": [{"id": "qwen3-4b", "name": "Qwen3 4B", "size_bytes": 123}]}
    _stub_reachable(monkeypatch, model_commands)
    monkeypatch.setattr(model_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(model_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == payload


# ── agent list --json ────────────────────────────────────────────────────────


def test_agent_list_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"agents": [{"name": "hermes", "status": "running"}]}
    _stub_reachable(monkeypatch, agent_commands)
    monkeypatch.setattr(agent_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(agent_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == payload


# ── slot show --json ─────────────────────────────────────────────────────────


def test_slot_show_json_emits_status_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {"name": "primary", "state": "ready"}
    cfg = {"model": {"default": "qwen3-4b"}, "port": 8081}
    _stub_reachable(monkeypatch, slot_commands)

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        return cfg if path.endswith("/config") else status

    monkeypatch.setattr(slot_commands, "api_get", fake_get)

    result = runner.invoke(slot_commands.app, ["show", "primary", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == {"status": status, "config": cfg}


# ── model show --json ────────────────────────────────────────────────────────


def test_model_show_json_emits_raw_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "qwen3-4b", "name": "Qwen3 4B", "path": "/mnt/ai-models/x.gguf"}
    _stub_reachable(monkeypatch, model_commands)
    monkeypatch.setattr(model_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(model_commands.app, ["show", "qwen3-4b", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == payload
