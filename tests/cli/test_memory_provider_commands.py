"""Tests for ``hal0 memory provider {list,status,set}``, the --from/--to
Hindsight<->Honcho migrate paths, ``sync-graph``, and ``honcho render-env``.

Mocks the API layer (``_shared`` helpers) and the migration engine so the
CLI runs fully offline, mirroring ``tests/cli/test_memory_graph_commands.py``.

feat/memory-bank-cli rebase note: ``migrate``'s ``--from``/``--to`` body and
its four helper functions (``_load_honcho_cli_config``, ``_migrate_state``,
``_run_migrate_hindsight_to_honcho``, ``_run_migrate_honcho_to_hindsight``)
moved from ``memory_commands.py`` into ``memory_migrate_commands.py`` so
``migrate unify`` could nest under the same ``migrate`` name — same
behaviour, verbatim body, just relocated. ``sync-graph``/``honcho
render-env`` (still in ``memory_commands.py``) import those helpers with a
local ``from ... import`` at call time, so patching them on
``memory_migrate_commands`` (not ``memory_commands``) is what actually
takes effect now; ``provider`` itself didn't move.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_commands, memory_migrate_commands

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_commands, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": [], "put": []}

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        calls["get"].append(path)
        return {
            "engines": {
                "hindsight": {"healthy": True, "url": "http://127.0.0.1:9177"},
                "honcho": {"healthy": False, "url": "http://127.0.0.1:8000"},
            },
            "agents": {"hermes": {"provider": "hindsight", "private": False}},
        }

    def fake_put(path: str, **kw: Any) -> dict[str, Any]:
        calls["put"].append((path, kw.get("json")))
        body = kw.get("json", {})
        return {
            "agent": body.get("agent"),
            "provider": body.get("provider"),
            "private": bool(body.get("private", False)),
            "restarted": True,
            "provisioned": True,
            "note": None,
        }

    monkeypatch.setattr(memory_commands, "api_get", fake_get)
    monkeypatch.setattr(memory_commands, "api_put", fake_put)
    return calls


@pytest.fixture
def stub_honcho_cfg(monkeypatch: pytest.MonkeyPatch):
    """Fake ``hal0.toml [honcho]`` config so migrate/sync-graph never touch disk."""
    cfg = SimpleNamespace(honcho=SimpleNamespace(port=8000, workspace="hal0", user_peer="operator"))
    monkeypatch.setattr(memory_migrate_commands, "_load_honcho_cli_config", lambda: cfg)

    class _FakeState:
        def __init__(self) -> None:
            self.saved = False

        def save(self) -> None:
            self.saved = True

    state = _FakeState()
    monkeypatch.setattr(memory_migrate_commands, "_migrate_state", lambda: state)
    return state


# ── provider list / status ──────────────────────────────────────────────────


def test_provider_list_renders_engines_and_agents(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["provider", "list"])
    assert result.exit_code == 0, result.output
    assert "hindsight" in result.output
    assert "honcho" in result.output
    assert "hermes" in result.output


def test_provider_status_alias_same_output(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["provider", "status"])
    assert result.exit_code == 0, result.output
    assert "hindsight" in result.output


def test_provider_list_json(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["provider", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["agents"]["hermes"]["provider"] == "hindsight"


# ── provider set ─────────────────────────────────────────────────────────────


def test_provider_set_sends_expected_body(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app, ["provider", "set", "hermes", "honcho", "--private"]
    )
    assert result.exit_code == 0, result.output
    assert stub_api["put"], "PUT should have been sent"
    path, body = stub_api["put"][-1]
    assert path == "/api/memory/provider"
    assert body == {"agent": "hermes", "provider": "honcho", "restart": True, "private": True}


def test_provider_set_no_restart(stub_api) -> None:
    result = runner.invoke(
        memory_commands.app, ["provider", "set", "hermes", "hindsight", "--no-restart"]
    )
    assert result.exit_code == 0, result.output
    _, body = stub_api["put"][-1]
    assert body["restart"] is False


def test_provider_set_rejects_unknown_provider(stub_api) -> None:
    result = runner.invoke(memory_commands.app, ["provider", "set", "hermes", "bogus"])
    assert result.exit_code != 0
    assert stub_api["put"] == []


# ── migrate --from/--to (engine stubbed) ────────────────────────────────────


def test_migrate_dry_run_hindsight_to_honcho_invokes_engine(
    monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_forward(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "shared": {"scanned": 3, "migrated": 3, "skipped": 0},
            "total": {"scanned": 3, "migrated": 3, "skipped": 0},
        }

    monkeypatch.setattr(memory_migrate_commands, "_run_migrate_hindsight_to_honcho", fake_forward)
    result = runner.invoke(
        memory_commands.app,
        [
            "migrate",
            "--from",
            "hindsight",
            "--to",
            "honcho",
            "--agent",
            "hermes",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["dry_run"] is True
    assert calls[0]["agent_id"] == "hermes"
    assert calls[0]["honcho_base"] == "http://127.0.0.1:8000"
    assert not stub_honcho_cfg.saved  # dry-run never persists state
    payload = json.loads(result.output)
    assert payload["total"]["migrated"] == 3


def test_migrate_defaults_engine_dry_run_false(
    monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_forward(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"total": {"scanned": 0, "migrated": 0, "skipped": 0}}

    monkeypatch.setattr(memory_migrate_commands, "_run_migrate_hindsight_to_honcho", fake_forward)
    result = runner.invoke(
        memory_commands.app, ["migrate", "--from", "hindsight", "--to", "honcho"]
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is False
    assert stub_honcho_cfg.saved  # non-dry-run persists state


def test_migrate_honcho_to_hindsight_passes_since(
    monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_reverse(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"scanned": 1, "migrated": 1, "skipped": 0, "watermark": "2026-07-11T00:00:00Z"}

    monkeypatch.setattr(memory_migrate_commands, "_run_migrate_honcho_to_hindsight", fake_reverse)
    result = runner.invoke(
        memory_commands.app,
        ["migrate", "--from", "honcho", "--to", "hindsight", "--since", "2026-07-01T00:00:00Z"],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["since"] == "2026-07-01T00:00:00Z"
    assert calls[0]["workspace"] == "hal0"


def test_migrate_rejects_same_engine(stub_honcho_cfg) -> None:
    result = runner.invoke(
        memory_commands.app, ["migrate", "--from", "hindsight", "--to", "hindsight"]
    )
    assert result.exit_code != 0


def test_migrate_legacy_cognee_path_unaffected(tmp_path) -> None:
    # No --from/--to → legacy cognee dry-run path, unrelated to Honcho.
    result = runner.invoke(
        memory_commands.app, ["migrate", "--cognee-dir", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"rows_total": 0, "rows_mapped": 0, "rows_unmapped": 0, "noop": True}


# ── sync-graph ───────────────────────────────────────────────────────────────


def test_sync_graph_invokes_reverse_engine(
    monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_reverse(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"scanned": 2, "migrated": 1, "skipped": 1, "watermark": "2026-07-11T00:00:00Z"}

    monkeypatch.setattr(memory_migrate_commands, "_run_migrate_honcho_to_hindsight", fake_reverse)
    result = runner.invoke(memory_commands.app, ["sync-graph", "--agent", "hermes", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[0]["dry_run"] is False
    assert calls[0]["agent_id"] == "hermes"
    assert stub_honcho_cfg.saved
    payload = json.loads(result.output)
    assert payload["migrated"] == 1


# ── honcho render-env ────────────────────────────────────────────────────────


def test_honcho_render_env_reports_status(monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg) -> None:
    fake_module = SimpleNamespace(
        apply_honcho_env=lambda cfg, restart=True: {
            "written": True,
            "changed": True,
            "restarted": restart,
            "error": None,
        }
    )
    monkeypatch.setitem(__import__("sys").modules, "hal0.memory.honcho_env", fake_module)
    result = runner.invoke(memory_commands.app, ["honcho", "render-env", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["written"] is True
    assert payload["restarted"] is True


def test_honcho_render_env_missing_module_dies(
    monkeypatch: pytest.MonkeyPatch, stub_honcho_cfg
) -> None:
    import sys

    monkeypatch.delitem(sys.modules, "hal0.memory.honcho_env", raising=False)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "hal0.memory.honcho_env":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = runner.invoke(memory_commands.app, ["honcho", "render-env"])
    assert result.exit_code != 0
