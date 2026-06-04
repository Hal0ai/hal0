"""Install-artifacts phase contract (issue #432).

``hal0 agent bootstrap hermes`` is a separate install path from
``AgentManager.install``. The provision pipeline wrote data/state but
never the three artifacts downstream components key off:

  * the manager seed at ``/etc/hal0/agents/hermes.toml``,
  * the driver env file at ``/etc/hal0/agents/hermes.env``,
  * ``runtime.json`` (embed token) under ``$HERMES_HOME``.

These tests pin the new ``install_artifacts`` phase: a fresh run writes
all three; re-runs are idempotent (the embed token does NOT rotate);
``--repair`` rewrites a fresh token; and the chat proxy's
``_load_embed_token`` finds the token the phase wrote.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp
from hal0.api.agents import chat_proxy


@pytest.fixture
def artifact_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hp.BootstrapState:
    """A BootstrapState + module path constants rooted under ``tmp_path``.

    Redirects the three artifact destinations off the real /etc + /var
    tree so the phase writes are hermetic.
    """
    hermes_home = tmp_path / "var" / "lib" / "hal0" / ".hermes"
    hermes_home.mkdir(parents=True)
    monkeypatch.setattr(
        hp, "INSTALL_SEED_PATH", tmp_path / "etc" / "hal0" / "agents" / "hermes.toml"
    )
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", tmp_path / "etc" / "hal0" / "agents" / "hermes.env")
    return hp.BootstrapState(hermes_home=str(hermes_home), agent_id="hermes-agent")


def test_phase_writes_all_three_artifacts(artifact_state: hp.BootstrapState) -> None:
    """A fresh run leaves seed TOML + driver env + runtime.json on disk."""
    result = hp._phase_install_artifacts(artifact_state)
    assert result.status is hp.PhaseStatus.OK

    seed_path = hp.INSTALL_SEED_PATH
    env_path = hp.DRIVER_ENV_PATH
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME

    assert seed_path.exists()
    assert env_path.exists()
    assert runtime_path.exists()

    # Seed parses + carries the manager-shape ``[agent]`` block.
    seed = tomllib.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["agent"]["name"] == "hermes"
    assert seed["agent"]["installed_at"]
    assert seed["agent"]["version_pin"] is False
    assert seed["data_dir"]

    # Driver env carries the canonical hal0 API URL the wrapper sources.
    env_body = env_path.read_text(encoding="utf-8")
    assert "HAL0_API_URL=" in env_body
    assert "HAL0_MCP_ADMIN_URL=" in env_body
    assert "HAL0_MCP_MEMORY_URL=" in env_body

    # runtime.json carries a non-empty token + is 0600.
    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert isinstance(data["token"], str) and data["token"]
    assert (runtime_path.stat().st_mode & 0o777) == 0o600


def test_token_is_stable_across_reruns(artifact_state: hp.BootstrapState) -> None:
    """Re-running without --repair must NOT rotate the embed token —
    otherwise a re-provision would break a running proxy mid-session."""
    hp._phase_install_artifacts(artifact_state)
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    token_1 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    result_2 = hp._phase_install_artifacts(artifact_state)
    token_2 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]
    assert token_1 == token_2
    assert result_2.details["token_wrote"] is False


def test_repair_rotates_token(artifact_state: hp.BootstrapState) -> None:
    """``--repair`` explicitly resets to known-good — a fresh token."""
    hp._phase_install_artifacts(artifact_state)
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    token_1 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    # Simulate the orchestrator's --repair flag (stashed on state.phases).
    artifact_state.phases["_repair_flag"] = {"status": "stub", "details": {}}
    hp._phase_install_artifacts(artifact_state)
    token_2 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]
    assert token_1 != token_2


def test_seed_write_preserves_operator_mcp_servers(artifact_state: hp.BootstrapState) -> None:
    """The seed TOML doubles as the MCP allow-list — the write must merge,
    never clobber, any operator-added ``[mcp.servers.*]`` blocks."""
    seed_path = hp.INSTALL_SEED_PATH
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        '[mcp.servers.custom]\nurl = "http://127.0.0.1:9000/mcp"\n',
        encoding="utf-8",
    )
    hp._phase_install_artifacts(artifact_state)

    seed = tomllib.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["mcp"]["servers"]["custom"]["url"] == "http://127.0.0.1:9000/mcp"
    # And the agent block was still written.
    assert seed["agent"]["name"] == "hermes"


def test_chat_proxy_finds_token_after_provision(
    artifact_state: hp.BootstrapState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end seam: chat_proxy._load_embed_token() resolves the token
    the install_artifacts phase wrote (previously always None — runtime.json
    had zero writers)."""
    hp._phase_install_artifacts(artifact_state)
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    expected = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    monkeypatch.setenv("HAL0_HERMES_RUNTIME_JSON", str(runtime_path))
    assert chat_proxy._load_embed_token() == expected
