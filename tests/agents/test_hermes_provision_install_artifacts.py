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


def test_phase_writes_all_three_artifacts(
    artifact_state: hp.BootstrapState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh run leaves seed TOML + driver env + runtime.json on disk."""
    # Deterministic: this phase now branches on the box service key, so pin
    # the ambient auth posture rather than inheriting whatever the test
    # process/host happens to carry.
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    result = hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))
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

    # The two builtin MCP servers are registered so mcp_wire's allow-list
    # gate (ADR-0013) doesn't filter them out, and AgentMCPClient has a
    # server to resolve tools/auth against. No admin key on this box (see
    # delenv above) → no auth block, matching AgentAuthConfig's kind="none"
    # default — tokenless, not absent.
    servers = seed["mcp"]["servers"]
    assert servers["hal0-admin"]["builtin"] is True
    assert servers["hal0-admin"]["enabled"] is True
    assert servers["hal0-memory"]["builtin"] is True
    assert "auth" not in servers["hal0-admin"]

    # Driver env carries the canonical hal0 API URL the wrapper sources.
    env_body = env_path.read_text(encoding="utf-8")
    assert "HAL0_API_URL=" in env_body
    assert "HAL0_MCP_ADMIN_URL=" in env_body
    assert "HAL0_MCP_MEMORY_URL=" in env_body
    assert "HAL0_MCP_TOKEN" not in env_body  # keyless box — see delenv above

    # runtime.json carries a non-empty token + is 0600.
    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert isinstance(data["token"], str) and data["token"]
    assert (runtime_path.stat().st_mode & 0o777) == 0o600


def test_phase_wires_builtin_mcp_auth_when_key_resolvable(
    artifact_state: hp.BootstrapState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a box admin key present, both artifacts carry the bearer wiring:
    the seed TOML declares auth.kind=bearer-from-env/env=HAL0_MCP_TOKEN, and
    the driver env actually sets that variable — the two halves
    AgentMCPClient.token_for() needs together."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "install-artifacts-admin-key")
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))

    seed = tomllib.loads(hp.INSTALL_SEED_PATH.read_text(encoding="utf-8"))
    for name in ("hal0-admin", "hal0-memory"):
        auth = seed["mcp"]["servers"][name]["auth"]
        assert auth["kind"] == "bearer-from-env"
        assert auth["env"] == "HAL0_MCP_TOKEN"

    env_body = hp.DRIVER_ENV_PATH.read_text(encoding="utf-8")
    assert "HAL0_MCP_TOKEN=install-artifacts-admin-key" in env_body
    assert (hp.DRIVER_ENV_PATH.stat().st_mode & 0o777) == 0o600

    # And the declared shape actually round-trips through AgentMCPClient.
    from hal0.agents.mcp_client import AgentMCPClient

    monkeypatch.setenv("HAL0_MCP_TOKEN", "install-artifacts-admin-key")
    client = AgentMCPClient.from_config_file(hp.INSTALL_SEED_PATH)
    assert client.token_for("hal0-admin") == "install-artifacts-admin-key"
    assert client.token_for("hal0-memory") == "install-artifacts-admin-key"


def test_token_is_stable_across_reruns(artifact_state: hp.BootstrapState) -> None:
    """Re-running without --repair must NOT rotate the embed token —
    otherwise a re-provision would break a running proxy mid-session."""
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    token_1 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    result_2 = hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))
    token_2 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]
    assert token_1 == token_2
    assert result_2.details["token_wrote"] is False


def test_repair_rotates_token(artifact_state: hp.BootstrapState) -> None:
    """``--repair`` explicitly resets to known-good — a fresh token."""
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    token_1 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    # The orchestrator's --repair flag arrives via ctx.repair (#702).
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state, repair=True))
    token_2 = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]
    assert token_1 != token_2


def test_seed_write_preserves_operator_mcp_servers(artifact_state: hp.BootstrapState) -> None:
    """The seed TOML doubles as the MCP allow-list — the write must merge,
    never clobber, any operator-added ``[mcp.servers.*]`` blocks, and must
    land the two builtins (hal0-admin, hal0-memory) alongside them."""
    seed_path = hp.INSTALL_SEED_PATH
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        '[mcp.servers.custom]\nurl = "http://127.0.0.1:9000/mcp"\n',
        encoding="utf-8",
    )
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))

    seed = tomllib.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["mcp"]["servers"]["custom"]["url"] == "http://127.0.0.1:9000/mcp"
    assert seed["mcp"]["servers"]["hal0-admin"]["builtin"] is True
    assert seed["mcp"]["servers"]["hal0-memory"]["builtin"] is True
    # And the agent block was still written.
    assert seed["agent"]["name"] == "hermes"


def test_chat_proxy_finds_token_after_provision(
    artifact_state: hp.BootstrapState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end seam: chat_proxy._load_embed_token() resolves the token
    the install_artifacts phase wrote (previously always None — runtime.json
    had zero writers)."""
    hp._phase_install_artifacts(hp._StepCtx(state=artifact_state))
    runtime_path = Path(artifact_state.hermes_home) / hp.RUNTIME_JSON_NAME
    expected = json.loads(runtime_path.read_text(encoding="utf-8"))["token"]

    monkeypatch.setenv("HAL0_HERMES_RUNTIME_JSON", str(runtime_path))
    assert chat_proxy._load_embed_token() == expected
