"""ADR-0013 MCP client surface for bundled agents.

Reads ``/etc/hal0/agents/<name>.toml``, loads tokens from
systemd-credential / env file, and exposes the three-tier tool
classification (allow / gated / blocked) to the agent driver.

This module is **transport-agnostic**: it doesn't speak MCP over the
wire itself. ADR-0013 §6 says Hermes's MCP client does the wire work;
the AgentMCPClient defined here is the *policy* layer that sits in
front of it. The agent driver wires this in via:

    client = AgentMCPClient.from_config_file("/etc/hal0/agents/hermes.toml")
    decision = client.classify("filesystem", "write_file")
    if decision == "blocked":
        raise ToolNotPermitted(...)
    elif decision == "gated":
        await approval_queue.enqueue(...)
    else:  # allow
        await wire_client.call(server="filesystem", tool="write_file", args=safe_args)

The wire-call site (Hermes) is owned by the Hermes teammate
(installer/agents/hermes.sh + the hermes provisioner). This module
only ships the policy + token-loading surface so the Hermes shim can
import it and stay schema-aware without re-implementing the rules.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import structlog

from hal0.config import paths as cfg_paths
from hal0.config.loader import load_agent_config
from hal0.config.schema import AgentConfig, MCPServerConfig

log = structlog.get_logger(__name__)


# ADR-0013 §4 — three-tier classification verdict.
ClassificationLiteral = Literal["allow", "gated", "blocked", "unknown_server", "unknown_tool"]


class ToolNotPermittedError(Exception):
    """Raised by :meth:`AgentMCPClient.guard` for hard-blocked tools.

    Maps to the wire-level ``tool.not_permitted`` error code per
    ADR-0013 §3. Agent drivers should catch + translate into whatever
    error envelope their LLM wire format expects.
    """

    def __init__(self, server: str, tool: str, reason: str = "tool blocked by allow-list"):
        super().__init__(f"{server}.{tool}: {reason}")
        self.server = server
        self.tool = tool
        self.reason = reason


class WorkspaceEscapeError(Exception):
    """Raised when filesystem-style MCP args try to escape the workspace.

    ADR-0013 §5: tool arguments that use ``../`` or pass absolute paths
    outside the workspace get rejected client-side BEFORE they hit the
    server. The agent driver translates this to a tool error visible to
    the LLM so it can retry with a corrected path.
    """

    def __init__(self, path: str, workspace: str):
        super().__init__(f"path {path!r} escapes workspace {workspace!r}")
        self.path = path
        self.workspace = workspace


class AgentMCPClient:
    """Per-agent policy layer over the MCP wire client.

    One instance per agent process. Holds the loaded AgentConfig +
    resolved tokens; exposes ``classify``, ``guard``, ``token_for``,
    and ``rewrite_path`` for the agent driver to consult before each
    tool call.

    The class is **synchronous** — every method is a pure function
    over loaded config. Wire IO lives in the agent driver. Tests can
    construct one directly with a fixture-built AgentConfig.
    """

    def __init__(self, config: AgentConfig, *, workspace: Path | None = None) -> None:
        """Build a policy client.

        :param config: Validated AgentConfig (see ADR-0013 §2 schema).
        :param workspace: Override the workspace root (tests use this
            with ``tmp_path``). When None, derived from
            ``config.agent.workspace`` if set, else from
            :func:`hal0.config.paths.agent_workspace_dir`.
        """
        self._config = config
        if workspace is not None:
            self._workspace = Path(workspace).resolve()
        elif config.agent.workspace.strip():
            self._workspace = Path(config.agent.workspace).resolve()
        else:
            self._workspace = cfg_paths.agent_workspace_dir(config.agent.name).resolve()

    # ── factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config_file(cls, path: str | Path) -> AgentMCPClient:
        """Load + validate the on-disk TOML, return a ready client."""
        p = Path(path)
        # load_agent_config takes the *name*, not the path; route around it.
        cfg = load_agent_config(agent_name=p.stem, path=p)
        return cls(cfg)

    @classmethod
    def from_agent_name(cls, name: str) -> AgentMCPClient:
        """Load by canonical agent name (``/etc/hal0/agents/<name>.toml``)."""
        cfg = load_agent_config(agent_name=name)
        return cls(cfg)

    # ── introspection ──────────────────────────────────────────────────────

    @property
    def agent_name(self) -> str:
        return self._config.agent.name

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def config(self) -> AgentConfig:
        return self._config

    def enabled_servers(self) -> dict[str, MCPServerConfig]:
        """Return the servers the agent is allowed to *connect* to.

        ADR-0013 §3 server-axis default-deny: a server not in the
        config or with ``enabled = false`` is unreachable. Builtins
        (hal0-admin / hal0-memory) flow through unchanged — they're
        always-allowed for bundled agents.
        """
        return {name: srv for name, srv in self._config.mcp.servers.items() if srv.enabled}

    # ── classification ─────────────────────────────────────────────────────

    def classify(self, server: str, tool: str) -> ClassificationLiteral:
        """Return the verdict for one (server, tool) pair.

        Verdicts:

        - ``unknown_server`` : server not in config (or disabled). Per
          ADR-0013 §3 the agent should treat this as ``blocked``, but
          we return a distinct verdict so callers can audit the cause
          (typo vs intentional removal).
        - ``unknown_tool``   : server is reachable, tool isn't on any
          list. Default-deny per ADR-0013 §3 tool-axis.
        - ``blocked``        : on ``tools.blocked``. Hard reject.
        - ``gated``          : on ``tools.gated``. Approval-queue path.
        - ``allow``          : on ``tools.allow``. Autonomous call.

        Blocked is checked FIRST so installer-pinned blocks beat user
        edits that placed the same tool on ``allow`` (ADR-0013 §4
        "installer-pinned blocks override user edits"). The pydantic
        ``ToolPolicy.lists_are_disjoint`` validator should prevent that
        overlap from ever reaching here, but defense-in-depth is cheap.
        """
        servers = self.enabled_servers()
        srv = servers.get(server)
        if srv is None:
            return "unknown_server"
        pol = srv.tools
        if tool in pol.blocked:
            return "blocked"
        if tool in pol.gated:
            return "gated"
        if tool in pol.allow:
            return "allow"
        return "unknown_tool"

    def guard(self, server: str, tool: str) -> ClassificationLiteral:
        """Same as :meth:`classify` but raise on hard-reject verdicts.

        Used at call-site to fail fast: the agent driver typically
        calls ``guard`` before serialising the tool call; on
        ``allow`` / ``gated`` the call proceeds (gated routes through
        the approval queue), on ``blocked`` / ``unknown_*`` the
        :class:`ToolNotPermittedError` bubbles up and the driver
        translates into the LLM's tool-error envelope.
        """
        verdict = self.classify(server, tool)
        if verdict in ("blocked", "unknown_server", "unknown_tool"):
            raise ToolNotPermittedError(server=server, tool=tool, reason=verdict)
        return verdict

    # ── token loading ──────────────────────────────────────────────────────

    def token_for(self, server: str) -> str | None:
        """Return the outbound bearer token for ``server`` or None.

        ADR-0013 §6: tokens load at process startup from env vars (or
        systemd-credential, which systemd materialises as an env var
        via ``LoadCredential=``); never live in TOML. This method is
        the single read site so audit-log scaffolding can wrap a
        per-token-fetch event around it if/when needed.

        Returns None when ``auth.kind != "bearer-from-env"`` (no token
        needed) OR when the env var is unset. The agent driver decides
        whether a missing token should fail bootstrap or skip the
        server gracefully — ADR-0013 §6 picks "log + continue" for
        non-builtin connection failures.
        """
        srv = self._config.mcp.servers.get(server)
        if srv is None:
            return None
        if srv.auth.kind != "bearer-from-env":
            return None
        env_name = srv.auth.env or ""
        if not env_name:
            return None
        token = os.environ.get(env_name)
        if token is None or not token.strip():
            log.warning(
                "hal0.agent.mcp_client.token_missing",
                agent=self.agent_name,
                server=server,
                env=env_name,
            )
            return None
        return token

    # ── path rewriting (filesystem MCP sandboxing) ──────────────────────────

    def rewrite_path(self, raw: str) -> Path:
        """Resolve ``raw`` against the workspace; raise on escape attempts.

        ADR-0013 §5: filesystem MCPs run with their server-side root
        pinned to the workspace, but the agent can still pass paths
        that try to escape (``../``, absolute paths outside the
        workspace). We resolve here BEFORE the server sees the call so
        the wire-level check happens in trust-aware code.

        ``raw`` is interpreted relative to the workspace if it's
        relative. Absolute paths are accepted only if they resolve
        underneath the workspace root.
        """
        ws = self._workspace
        p = Path(raw)
        if not p.is_absolute():
            p = ws / p
        resolved = p.resolve()
        # is_relative_to is 3.9+ and pydantic supports our Python pin.
        try:
            resolved.relative_to(ws)
        except ValueError as exc:
            raise WorkspaceEscapeError(path=raw, workspace=str(ws)) from exc
        return resolved


# ── convenience: classify a batch of tools at once ────────────────────────────


def classify_many(
    client: AgentMCPClient,
    pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], ClassificationLiteral]:
    """Bulk-classify a list of (server, tool) pairs.

    Used by the dashboard's read-only per-agent view to surface every
    classification at once without N HTTP calls.
    """
    return {(srv, tool): client.classify(srv, tool) for srv, tool in pairs}


__all__ = [
    "AgentMCPClient",
    "ClassificationLiteral",
    "ToolNotPermittedError",
    "WorkspaceEscapeError",
    "classify_many",
]
