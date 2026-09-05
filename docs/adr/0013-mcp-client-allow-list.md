# ADR-0013: Per-agent MCP client allow-list, default-deny on server and tool

## Status

**ACCEPTED (2026-05-23).** Reconstructed here against the current state;
the original write-up lived in the gitignored
`docs/internal/adr/0013-mcp-client-allow-list.md` (see `ARCHITECTURE.md`
"Decision records").

## Context

hal0 is an MCP *server* host — bundled agents connect into the
`hal0-admin` and `hal0-memory` MCP servers (ADR-0004). The reverse
direction needed its own rule: bundled agents are MCP *clients* too, and
can be pointed at external MCP servers (filesystem, GitHub, third-party
knowledge bases, user-installed MCPs). Left unscoped, an agent talking to
an untrusted external MCP server, or calling a destructive tool on one it
already trusts, is a straightforward prompt-injection footgun.

## Decision

**Default-deny on two independent axes — which servers an agent may
connect to, and which tools it may call on each — configured per agent,
never globally.**

- **Config lives at `/etc/hal0/agents/<name>.toml`**, one file per agent,
  under `[mcp.servers.<name>]` blocks
  (`src/hal0/agents/hermes_provision.py:2548` reads `[mcp.servers.*]`
  from this file). A server not listed is unreachable — there is no
  discover-and-connect fallback.
- **Three-tier tool classification per server**
  (`ToolPolicy`, `src/hal0/config/schema.py:2726`): `allow` (autonomous
  call), `gated` (enqueues through the same approval queue ADR-0004
  established), `blocked` (hard-rejected client-side, never reaches the
  server). The three lists must be disjoint — a load-time
  `ValidationError` catches an operator TOML edit that leaves a tool in
  two lists rather than silently picking a winner.
  A server with no `tools` block has zero callable tools by construction
  — the same default-deny posture applies at the tool axis as at the
  server axis.
- **Installer-pinned blocks are load-bearing.** A bundled agent's
  installer can put a tool (e.g. `delete_repo` on a GitHub MCP) in
  `blocked`; the dashboard cannot move it out. Only a direct, traceable
  TOML edit can.
- **Built-in servers (`hal0-admin`, `hal0-memory`) are always reachable**
  for a bundled agent and carry the identity header
  (`mcp_servers.<name>.headers.X-hal0-Agent`,
  `hermes_provision.py:2020`) that scopes memory writes into the
  agent's own `private:<agent_id>` namespace (ADR-0005).

## Consequences

- Server-axis and tool-axis default-deny means a freshly registered
  external MCP server is inert until an operator explicitly reviews and
  allow-lists its tools — the safe failure mode is "does nothing," not
  "does everything."
- The three-tier split (vs. a plain allow/deny) costs one more concept
  than a binary system, in exchange for letting an agent use a risky-but-
  needed tool (`gated`) without either fully trusting it (`allow`) or
  losing it entirely (`blocked`).
- `AgentConfig` / `MCPServerConfig` / `ToolPolicy`
  (`src/hal0/config/schema.py`) and `src/hal0/agents/mcp_client.py` are
  the schema and enforcement point this ADR called for; both exist in the
  current tree, so the "Pending items" in the original write-up have
  landed.

## References

- `src/hal0/config/schema.py:2726` (`ToolPolicy`), `:2796`
  (`MCPServerConfig`), `:2890` (`AgentConfig`)
- `src/hal0/agents/mcp_client.py` — per-agent MCP client enforcement
- `src/hal0/agents/hermes_provision.py:2548,2840-2988` (`_phase_mcp_wire`)
  — reads `[mcp.servers.*]`, probes each allowed connection
- `ui/src/api/hooks/useAgentMcpClients.ts` — dashboard per-agent
  MCP-client allow-list view
- ADR-0004 — bundled agents, the approval-queue surface `gated` reuses
- ADR-0005 — the `private:<agent_id>` namespace `X-hal0-Agent` routes into
