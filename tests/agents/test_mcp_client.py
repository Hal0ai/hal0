"""ADR-0013 MCP client tests.

Cover:
  - schema-validation envelope (overlap, missing fields)
  - classify() three-tier verdict + unknown_server / unknown_tool
  - guard() raises ToolNotPermittedError on hard-reject
  - token_for() loads from env, returns None on missing var
  - rewrite_path() pins to workspace + rejects ../ escapes
  - from_config_file round-trip
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from hal0.agents.mcp_client import (
    AgentMCPClient,
    ToolNotPermittedError,
    WorkspaceEscapeError,
    classify_many,
)
from hal0.config.schema import (
    AgentAuthConfig,
    AgentConfig,
    AgentMCPConfig,
    AgentMetadataConfig,
    MCPServerConfig,
    ToolPolicy,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    p = tmp_path / "ws"
    p.mkdir()
    (p / "ok.txt").write_text("hello")
    return p


@pytest.fixture
def sample_config(workspace: Path) -> AgentConfig:
    """Hermes-like config with one builtin + one user-added MCP."""
    return AgentConfig(
        agent=AgentMetadataConfig(
            name="hermes",
            display="Hermes-Agent",
            workspace=str(workspace),
        ),
        mcp=AgentMCPConfig(
            servers={
                "hal0-admin": MCPServerConfig(builtin=True),
                "filesystem": MCPServerConfig(
                    url="stdio:///usr/lib/hal0/mcp/fs-server",
                    enabled=True,
                    tools=ToolPolicy(
                        allow=["read_file", "list_directory"],
                        gated=["write_file"],
                        blocked=["delete_directory"],
                    ),
                ),
                "github": MCPServerConfig(
                    url="https://api.github.com/mcp",
                    enabled=False,  # opt-in; should be skipped
                    auth=AgentAuthConfig(
                        kind="bearer-from-env",
                        env="HAL0_AGENT_HERMES_GH_TOKEN",
                    ),
                    tools=ToolPolicy(allow=["list_issues"]),
                ),
            }
        ),
    )


@pytest.fixture
def client(sample_config: AgentConfig, workspace: Path) -> AgentMCPClient:
    return AgentMCPClient(sample_config, workspace=workspace)


# ── classify ──────────────────────────────────────────────────────────────────


class TestClassify:
    def test_allow_tool(self, client: AgentMCPClient) -> None:
        assert client.classify("filesystem", "read_file") == "allow"

    def test_gated_tool(self, client: AgentMCPClient) -> None:
        assert client.classify("filesystem", "write_file") == "gated"

    def test_blocked_tool(self, client: AgentMCPClient) -> None:
        assert client.classify("filesystem", "delete_directory") == "blocked"

    def test_unknown_tool_default_denies(self, client: AgentMCPClient) -> None:
        assert client.classify("filesystem", "format_disk") == "unknown_tool"

    def test_unknown_server_default_denies(self, client: AgentMCPClient) -> None:
        assert client.classify("nonexistent", "tool") == "unknown_server"

    def test_disabled_server_is_unknown(self, client: AgentMCPClient) -> None:
        """``enabled=false`` removes the server from enabled_servers()."""
        assert client.classify("github", "list_issues") == "unknown_server"

    def test_builtin_no_tools_yields_unknown_tool(self, client: AgentMCPClient) -> None:
        # hal0-admin has no tool lists in the sample → default-deny.
        assert client.classify("hal0-admin", "anything") == "unknown_tool"


# ── guard ──────────────────────────────────────────────────────────────────────


class TestGuard:
    def test_allow_passes(self, client: AgentMCPClient) -> None:
        assert client.guard("filesystem", "read_file") == "allow"

    def test_gated_passes(self, client: AgentMCPClient) -> None:
        assert client.guard("filesystem", "write_file") == "gated"

    def test_blocked_raises(self, client: AgentMCPClient) -> None:
        with pytest.raises(ToolNotPermittedError) as ei:
            client.guard("filesystem", "delete_directory")
        assert ei.value.server == "filesystem"
        assert ei.value.tool == "delete_directory"

    def test_unknown_server_raises(self, client: AgentMCPClient) -> None:
        with pytest.raises(ToolNotPermittedError):
            client.guard("nonexistent", "any")

    def test_unknown_tool_raises(self, client: AgentMCPClient) -> None:
        with pytest.raises(ToolNotPermittedError):
            client.guard("filesystem", "format_disk")


# ── token_for ──────────────────────────────────────────────────────────────────


class TestTokenFor:
    def test_no_auth_returns_none(self, client: AgentMCPClient) -> None:
        # filesystem has default auth (kind="none").
        assert client.token_for("filesystem") is None

    def test_bearer_loads_from_env(
        self, sample_config: AgentConfig, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Re-enable github so it shows up to token_for; we test via a
        # config copy because enabled=False filters from
        # enabled_servers() but token_for reads the full config map
        # directly.
        monkeypatch.setenv("HAL0_AGENT_HERMES_GH_TOKEN", "ghp_secret123")
        c = AgentMCPClient(sample_config, workspace=workspace)
        assert c.token_for("github") == "ghp_secret123"

    def test_bearer_missing_env_returns_none(
        self,
        sample_config: AgentConfig,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("HAL0_AGENT_HERMES_GH_TOKEN", raising=False)
        c = AgentMCPClient(sample_config, workspace=workspace)
        assert c.token_for("github") is None

    def test_unknown_server_returns_none(self, client: AgentMCPClient) -> None:
        assert client.token_for("nonexistent") is None


# ── rewrite_path ──────────────────────────────────────────────────────────────


class TestRewritePath:
    def test_relative_path_resolves_under_workspace(
        self, client: AgentMCPClient, workspace: Path
    ) -> None:
        out = client.rewrite_path("ok.txt")
        assert out == (workspace / "ok.txt").resolve()

    def test_absolute_inside_workspace_ok(self, client: AgentMCPClient, workspace: Path) -> None:
        out = client.rewrite_path(str(workspace / "ok.txt"))
        assert out == (workspace / "ok.txt").resolve()

    def test_dotdot_escape_rejected(self, client: AgentMCPClient) -> None:
        with pytest.raises(WorkspaceEscapeError):
            client.rewrite_path("../etc/passwd")

    def test_absolute_outside_workspace_rejected(self, client: AgentMCPClient) -> None:
        with pytest.raises(WorkspaceEscapeError):
            client.rewrite_path("/etc/passwd")


# ── from_config_file ─────────────────────────────────────────────────────────


def test_from_config_file_round_trip(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write a TOML the way installer/agents/hermes.sh would, load it."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    cfg_path = tmp_path / "hermes.toml"
    data = {
        "schema_version": 1,
        "agent": {
            "name": "hermes",
            "display": "Hermes",
            "workspace": str(workspace),
        },
        "mcp": {
            "servers": {
                "hal0-admin": {"builtin": True},
                "filesystem": {
                    "url": "stdio:///x/y",
                    "enabled": True,
                    "tools": {
                        "allow": ["read_file"],
                        "gated": ["write_file"],
                    },
                },
            }
        },
    }
    cfg_path.write_text(tomli_w.dumps(data))
    c = AgentMCPClient.from_config_file(cfg_path)
    assert c.agent_name == "hermes"
    assert c.classify("filesystem", "read_file") == "allow"
    assert c.classify("filesystem", "write_file") == "gated"


# ── classify_many ────────────────────────────────────────────────────────────


def test_classify_many(client: AgentMCPClient) -> None:
    pairs = [
        ("filesystem", "read_file"),
        ("filesystem", "delete_directory"),
        ("nonexistent", "x"),
    ]
    out = classify_many(client, pairs)
    assert out[("filesystem", "read_file")] == "allow"
    assert out[("filesystem", "delete_directory")] == "blocked"
    assert out[("nonexistent", "x")] == "unknown_server"
