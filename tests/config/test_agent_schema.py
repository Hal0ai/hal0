"""ADR-0013 agent-config schema tests.

Pins:

  - schema_version field exists + rejects future versions.
  - agent.name is lowercase-alphanumeric+hyphen, max 32 chars.
  - ToolPolicy.lists_are_disjoint rejects overlap (with the offending names).
  - AgentAuthConfig requires env when kind=bearer-from-env.
  - MCPServerConfig: external requires url; builtin does not.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    AGENT_CONFIG_SCHEMA_VERSION,
    AgentAuthConfig,
    AgentConfig,
    AgentMCPConfig,
    AgentMetadataConfig,
    MCPServerConfig,
    ToolPolicy,
)

# ── AgentConfig schema version ───────────────────────────────────────────────


class TestSchemaVersion:
    def test_default_matches_constant(self) -> None:
        cfg = AgentConfig(agent=AgentMetadataConfig(name="hermes"))
        assert cfg.schema_version == AGENT_CONFIG_SCHEMA_VERSION

    def test_future_version_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            AgentConfig(
                schema_version=AGENT_CONFIG_SCHEMA_VERSION + 99,
                agent=AgentMetadataConfig(name="hermes"),
            )
        assert "newer than this hal0" in str(ei.value)


# ── AgentMetadataConfig.name ─────────────────────────────────────────────────


class TestAgentName:
    def test_valid_simple(self) -> None:
        AgentMetadataConfig(name="hermes")

    def test_valid_with_hyphen_and_digits(self) -> None:
        AgentMetadataConfig(name="pi-coder-2")

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataConfig(name="Hermes")

    def test_starts_with_hyphen_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataConfig(name="-bad")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataConfig(name="")

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataConfig(name="a" * 33)


# ── ToolPolicy ───────────────────────────────────────────────────────────────


class TestToolPolicy:
    def test_default_empty_lists(self) -> None:
        p = ToolPolicy()
        assert p.allow == []
        assert p.gated == []
        assert p.blocked == []

    def test_disjoint_lists_ok(self) -> None:
        ToolPolicy(allow=["a", "b"], gated=["c"], blocked=["d"])

    def test_allow_gated_overlap_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ToolPolicy(allow=["write_file"], gated=["write_file"])
        msg = str(ei.value)
        assert "allow" in msg and "gated" in msg
        assert "write_file" in msg

    def test_allow_blocked_overlap_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ToolPolicy(allow=["delete"], blocked=["delete"])
        assert "delete" in str(ei.value)

    def test_gated_blocked_overlap_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ToolPolicy(gated=["x"], blocked=["x"])
        assert "x" in str(ei.value)

    def test_overlap_error_lists_all_offenders_sorted(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ToolPolicy(allow=["b", "a"], gated=["a", "b"])
        # Sorted for determinism — test that 'a' appears before 'b'.
        msg = str(ei.value)
        assert msg.index("'a'") < msg.index("'b'")


# ── AgentAuthConfig ──────────────────────────────────────────────────────────


class TestAgentAuthConfig:
    def test_default_none_ok(self) -> None:
        a = AgentAuthConfig()
        assert a.kind == "none"
        assert a.env is None

    def test_bearer_requires_env(self) -> None:
        with pytest.raises(ValidationError) as ei:
            AgentAuthConfig(kind="bearer-from-env")
        assert "auth.env" in str(ei.value)

    def test_bearer_with_env_ok(self) -> None:
        a = AgentAuthConfig(kind="bearer-from-env", env="HAL0_TOK")
        assert a.env == "HAL0_TOK"

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentAuthConfig(kind="oauth-magic")


# ── MCPServerConfig ──────────────────────────────────────────────────────────


class TestMCPServerConfig:
    def test_external_without_url_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            MCPServerConfig(builtin=False)
        assert "url required" in str(ei.value)

    def test_builtin_without_url_ok(self) -> None:
        srv = MCPServerConfig(builtin=True)
        assert srv.url is None

    def test_external_with_url_ok(self) -> None:
        srv = MCPServerConfig(url="stdio:///x")
        assert srv.url == "stdio:///x"

    def test_round_trip_dump(self) -> None:
        srv = MCPServerConfig(
            url="stdio:///x",
            tools=ToolPolicy(allow=["a"], blocked=["b"]),
            auth=AgentAuthConfig(kind="bearer-from-env", env="TOK"),
        )
        dumped = srv.model_dump()
        MCPServerConfig.model_validate(dumped)


# ── AgentConfig full round trip ──────────────────────────────────────────────


def test_full_round_trip() -> None:
    cfg = AgentConfig(
        agent=AgentMetadataConfig(name="hermes", display="Hermes"),
        mcp=AgentMCPConfig(
            servers={
                "hal0-admin": MCPServerConfig(builtin=True),
                "filesystem": MCPServerConfig(
                    url="stdio:///fs",
                    tools=ToolPolicy(allow=["read_file"], gated=["write_file"]),
                ),
            }
        ),
    )
    dumped = cfg.model_dump()
    rebuilt = AgentConfig.model_validate(dumped)
    assert rebuilt.agent.name == "hermes"
    assert rebuilt.mcp.servers["filesystem"].tools.allow == ["read_file"]
