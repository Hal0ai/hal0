"""Unit tests for the turnstone bootstrap pipeline.

Pure config builders are tested directly; the full pipeline runs against
a fake IO bundle + tmp-redirected paths so no network, subprocess, or
/var/lib writes happen.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.agents import turnstone_provision as tp
from hal0.agents.turnstone_provision import TurnstoneIO, TurnstoneState

# ── pure builders ────────────────────────────────────────────────────────────


def _slots() -> list[dict]:
    # Only type=="llm" slots with a model_id are chat backends. The rest
    # (embed/rerank/tts/etc.) must NOT be mapped as models — this mirrors the
    # real /api/slots shape that the on-box test surfaced.
    return [
        {"name": "agent", "type": "llm", "model_id": "m-agent", "context_length": 32768},
        {"name": "code", "type": "llm", "model_id": "m-code"},
        {"name": "embed", "type": "embedding", "model_id": "m-e"},
        {"name": "rerank", "type": "rerank", "model_id": "m-r"},
        {"name": "tts", "type": "tts"},
        {"name": "vision", "type": "image", "model_id": "m-v"},
    ]


def test_model_blocks_maps_chat_slots_only() -> None:
    # contexts are keyed by alias (== the /v1/models id), matching _fetch_model_contexts.
    blocks = tp._model_blocks(_slots(), {"code": 16384}, api_base="http://h:8080")
    assert set(blocks) == {"agent", "code"}  # embed/rerank/tts/vision excluded
    assert blocks["agent"]["context_window"] == 32768
    assert blocks["code"]["context_window"] == 16384
    assert blocks["agent"]["base_url"] == "http://h:8080/v1"


def test_model_blocks_guarantees_default_anchor_when_no_slots() -> None:
    blocks = tp._model_blocks([], {}, api_base="http://h:8080")
    assert "agent" in blocks


def test_build_config_shape() -> None:
    cfg = tp.build_config(
        slots=_slots(),
        contexts={},
        persona="be good",
        api_base="http://h:8080",
        db_url="/x/t.db",
        mcp_config_path="/x/mcp.json",
    )
    assert cfg["api"]["base_url"] == "http://h:8080/v1"
    assert "api_key" not in cfg["api"]  # secrets never in config
    assert cfg["session"]["instructions"] == "be good"
    assert cfg["judge"]["enabled"] is True
    assert cfg["tools"]["skip_permissions"] is False
    assert cfg["mcp"]["config_path"] == "/x/mcp.json"
    assert cfg["database"]["url"] == "/x/t.db"


def test_build_mcp_servers_scopes_by_agent_header() -> None:
    servers = tp.build_mcp_servers(api_base="http://h:8080", bearer="tok")["mcpServers"]
    assert set(servers) == {"hal0-memory", "hal0-admin"}
    mem = servers["hal0-memory"]
    assert mem["url"] == "http://h:8080/mcp/memory/mcp"
    assert mem["headers"]["X-hal0-Agent"] == "turnstone"
    assert mem["headers"]["Authorization"] == "Bearer tok"


def test_build_mcp_servers_omits_auth_without_bearer() -> None:
    servers = tp.build_mcp_servers(api_base="http://h:8080", bearer=None)["mcpServers"]
    assert "Authorization" not in servers["hal0-admin"]["headers"]


def test_phase_graph_validates_at_import() -> None:
    # Importing the module already ran validate_phase_graph; assert the shape.
    assert tp.PHASE_NAMES[0] == "preflight"
    assert "config_write" in tp.PHASE_NAMES
    assert tp.PHASE_NAMES[-1] == "self_report"


# ── full pipeline with fake IO ───────────────────────────────────────────────


@pytest.fixture
def tmp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every module-level path in turnstone_provision under tmp."""
    home = tmp_path / ".turnstone"
    monkeypatch.setattr(tp, "TURNSTONE_HOME", home)
    monkeypatch.setattr(tp, "TURNSTONE_CONFIG_PATH", home / "config.toml")
    monkeypatch.setattr(tp, "MCP_SERVERS_JSON", home / "mcp-servers.json")
    monkeypatch.setattr(tp, "PERSONA_PATH", home / "persona.txt")
    monkeypatch.setattr(tp, "DATA_DIR", tmp_path / "agents" / "turnstone")
    monkeypatch.setattr(tp, "SQLITE_DB", tmp_path / "agents" / "turnstone" / "turnstone.db")
    monkeypatch.setattr(tp, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(tp, "INSTALL_SEED_PATH", tmp_path / "etc" / "turnstone.toml")
    monkeypatch.setattr(tp, "DRIVER_ENV_PATH", tmp_path / "etc" / "turnstone.env")
    monkeypatch.setattr(tp, "SECRETS_ENV_PATH", tmp_path / "secrets" / "turnstone.env")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    bin_path = venv / "bin" / "turnstone"
    server = venv / "bin" / "turnstone-server"
    for p in (bin_path, server):
        p.write_text("#!/bin/sh\necho turnstone 9.9\n")
        p.chmod(0o755)
    monkeypatch.setattr(tp, "VENV", venv)
    monkeypatch.setattr(tp, "MANAGED_BIN", bin_path)
    monkeypatch.setattr(tp, "SERVER_BIN", server)
    monkeypatch.setattr(tp, "CLI_SHIM", bin_path)
    return tmp_path


def _fake_io() -> TurnstoneIO:
    def http_get(url: str, **k: object) -> int:
        return 200

    def fetch_slots() -> list[dict]:
        return _slots()

    def fetch_ctx() -> dict:
        return {"code": 16384}  # keyed by alias, like _fetch_model_contexts

    def probe(url: str, **k: object) -> dict:
        return {"ok": True, "tools": [1, 2, 3]}

    def memcall(method: str, params: dict, **k: object) -> dict:
        return {"ok": True, "result": {}}

    def run(argv: list[str], **k: object) -> object:
        return SimpleNamespace(returncode=0, stdout="turnstone 9.9", stderr="")

    return TurnstoneIO(
        http_get=http_get,
        fetch_slots=fetch_slots,
        fetch_model_contexts=fetch_ctx,
        probe_mcp_server=probe,
        mcp_memory_call=memcall,
        run=run,
    )


# Phases that touch paths outside the tmp redirect (/etc/hal0, chown).
_SKIP = ("install", "context_link", "ownership_reconcile")


def test_full_pipeline_writes_all_artifacts(tmp_paths: Path) -> None:
    res = tp.run(state_root=tp.STATE_ROOT, io=_fake_io(), skip_phases=_SKIP)
    assert res.failed == []
    assert res.aborted is False

    cfg = tomllib.loads(tp.TURNSTONE_CONFIG_PATH.read_text())
    assert cfg["api"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert set(cfg["models"]) == {"agent", "code"}

    servers = json.loads(tp.MCP_SERVERS_JSON.read_text())["mcpServers"]
    assert set(servers) == {"hal0-memory", "hal0-admin"}

    assert tp.INSTALL_SEED_PATH.exists()
    assert tp.SECRETS_ENV_PATH.exists()
    assert oct(tp.SECRETS_ENV_PATH.stat().st_mode)[-3:] == "600"

    state = json.loads((tp.STATE_ROOT / "provision.json").read_text())
    assert state["phases"]["config_write"]["status"] == "ok"
    assert state["phases"]["smoke_tests"]["status"] == "ok"


def test_preflight_fails_when_api_unreachable(tmp_paths: Path) -> None:
    io = _fake_io()
    io = tp.TurnstoneIO(**{**io.__dict__, "http_get": lambda url, **k: 0})
    res = tp.run(state_root=tp.STATE_ROOT, io=io, skip_phases=_SKIP)
    assert "preflight" in res.failed


def test_rerun_is_idempotent(tmp_paths: Path) -> None:
    io = _fake_io()
    tp.run(state_root=tp.STATE_ROOT, io=io, skip_phases=_SKIP)
    res2 = tp.run(state_root=tp.STATE_ROOT, io=io, skip_phases=_SKIP)
    # Every non-skipped, non-always-run phase should be skipped on the rerun.
    assert res2.failed == []
    # config_write already ok → skipped.
    assert "config_write" in res2.skipped


def test_state_records_turnstone_fields(tmp_paths: Path) -> None:
    tp.run(state_root=tp.STATE_ROOT, io=_fake_io(), skip_phases=_SKIP)
    st = TurnstoneState.load(tp.STATE_ROOT)
    assert st is not None
    assert st.agent_id == "turnstone"
    assert st.turnstone_version == "turnstone 9.9"
