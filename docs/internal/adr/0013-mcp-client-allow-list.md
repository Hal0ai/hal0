# ADR 0013 — MCP-client allow-list for bundled agents (v0.3)

- **Status:** Accepted
- **Date:** 2026-05-23
- **Drivers:** PLAN.md §1 v0.3 stream #5 "MCP client side of hal0 — bundled agents reach external MCP servers with per-agent allow-list, filesystem scoped to `/var/lib/hal0/agents/<name>/workspace`"; v0.3 ships-when criterion
- **Related:** ADR-0004 (Agents v0.2 — MCP server side), ADR-0011 (agent identity cards), ADR-0012 (remove auth and Caddy — supersedes ADR-0001; the `auth.kind = "bearer-from-env"` examples in this ADR cover OUTBOUND auth from agents to external MCP servers like GitHub, NOT hal0's inbound auth, which doesn't exist post-ADR-0012)

## Context

v0.2 made hal0 an **MCP server host** — bundled agents (Hermes-Agent
in v0.3) connect *into* hal0-admin + hal0-memory MCPs. The reverse
direction is still missing: bundled agents are **MCP clients** in
their own right and the v0.3 spec wants them to reach external MCP
servers (file system, github, search, third-party knowledge bases,
custom user-installed MCPs) under per-agent scoped permissions.

The shape currently missing:

1. **Where the allow-list lives on disk** and what schema it uses.
2. **What "allow" means** — server-level (which servers are reachable
   at all), tool-level (which of a server's tools the agent can call),
   or both.
3. **Default-deny vs default-allow.** Home-AI single-user trust posture
   leans permissive; agent-tool-access security leans restrictive.
4. **Sandbox shape.** PLAN.md §1 names
   `/var/lib/hal0/agents/<name>/workspace` for filesystem; the
   network surface is unspecified.
5. **Approval integration.** ADR-0004's approval queue gates capital-D
   destructive actions on hal0-admin MCP. Does it extend to external
   MCPs?
6. **Bootstrap path.** Hermes bootstrap (ADR-0011 + the bootstrap plan
   §3 `mcp_wire` phase) writes the initial config. What does it write?
7. **Dashboard surface.** v3's MCP page (`/agents/mcp`) currently
   manages MCP *servers* (which to install + which to enable). It
   needs a parallel "clients" view that shows, per agent, what's
   allowed.

## Options considered

| Option | Reason rejected (or accepted) |
|---|---|
| **Single `/etc/hal0/agents.toml`** with all agents' allow-lists in one file | Rejected. Couples per-agent diffs to a shared file, friction for installer-managed agents vs user-added agents. |
| **Per-agent file at `/etc/hal0/agents/<name>.toml`** | ACCEPTED. Mirrors slots/upstreams pattern. Each bundled-agent installer drops its own file; user-added agents do likewise. |
| **Embed allow-list inside `<name>`'s service unit env** | Rejected. Auth/permission data shouldn't live in systemd env files (no comments, no structured nesting, hard to dashboard-edit). |
| **Allow-list in Cognee `agents` dataset (ADR-0011)** with the identity card | Rejected for v0.3. Identity card is a *projection* of running state, not configuration. Allow-list is configuration. Mixing the two muddies ADR-0011's contract. Could converge in v0.4 if the dashboard surface needs a unified view. |
| **Server-only allow-list** (allow whole server or nothing) | Rejected. Insufficiently granular for high-value MCPs that mix safe + risky tools (e.g., github-mcp's read tools vs `delete_repo`). |
| **Server + tool allow-list, default-deny on both** | ACCEPTED. Matches OAuth-scope-style mental model. Mirrors ADR-0004's "autonomous vs gated" tool split. |

## Decision

### 1. Config lives at `/etc/hal0/agents/<name>.toml`

- One file per agent (`hermes.toml`, `pi-coder.toml`, etc.).
- Owned by the installer for bundled agents; user-managed for
  user-added agents.
- Preserved across `hal0 update` (same as everything under `/etc/hal0/`).
- Schema validated at agent bootstrap time + on dashboard-edit save.

### 2. Schema

```toml
# /etc/hal0/agents/hermes.toml
schema_version = 1

[agent]
name        = "hermes"
display     = "Hermes-Agent"
# Filesystem sandbox root. Agent processes are chrooted/bind-mounted
# to see only this path as their workspace. Outside writes require
# approval (per ADR-0004 §2 destructive-action gating).
workspace   = "/var/lib/hal0/.hermes/workspace"

# ---------------------------------------------------------------
# MCP servers the agent is permitted to *connect* to.
# Default-deny: a server not listed here is unreachable.
# ---------------------------------------------------------------

[mcp.servers.hal0-admin]
# Built-in. Always allowed for bundled agents; can't be removed
# from a bundled agent's config without an explicit override.
builtin = true

[mcp.servers.hal0-memory]
builtin = true

[mcp.servers.filesystem]
# User-added local MCP. Connection only — tool gates below.
url      = "stdio:///usr/lib/hal0/mcp/filesystem-server"
enabled  = true
# Per-tool allow-list. Default-deny.
tools.allow = [
    "read_file",
    "list_directory",
    "search_files",
]
# Tools listed here go through the ADR-0004 approval queue even
# though they're on `tools.allow`. Use for destructive-but-needed.
tools.gated = [
    "write_file",
]

[mcp.servers.github]
url      = "https://api.github.com/mcp"
enabled  = false   # opt-in
auth.kind = "bearer-from-env"
auth.env  = "HAL0_AGENT_HERMES_GITHUB_TOKEN"
tools.allow = [
    "list_issues",
    "get_pr",
    "search_code",
]
tools.gated = [
    "create_pr",
    "post_issue_comment",
]
# Tools listed here are explicitly blocked — installer can pin
# "never allow" tools even if a user later expands tools.allow.
tools.blocked = [
    "delete_repo",
    "delete_branch",
]
```

### 3. Default-deny on both axes

- **Server axis.** An MCP server not listed in `[mcp.servers.*]` is
  unreachable by the agent. No "discover and connect" fallback.
- **Tool axis.** Inside an allowed server, only tools in
  `tools.allow` (or `tools.gated`) are callable. Anything else returns
  `tool.not_permitted` to the agent.

### 4. Three-tier tool classification

| Tier | Behavior | Goes through approval queue? |
|---|---|---|
| `tools.allow`   | Autonomous call | No |
| `tools.gated`   | Call enqueued, awaits user approval | Yes (ADR-0004 surface) |
| `tools.blocked` | Hard reject at the client; never reaches the server | N/A |

- **Migration path.** A tool starting `gated` and proving stable can
  be moved to `allow` by the user via dashboard or by editing the
  TOML. Migration the other direction is permitted too.
- **Installer-pinned blocks override user edits.** If the installer
  put `delete_repo` in `tools.blocked` for a bundled agent, the
  dashboard can't move it out — only direct TOML edit (loud, traceable
  in git) can.

### 5. Filesystem sandbox

- Agent processes launched via the agent driver (`src/hal0/agents/*`)
  get `workspace = /var/lib/hal0/agents/<name>/workspace` as their
  effective HOME + CWD.
- Filesystem MCPs (when allowed) get their server-side root pinned
  to that same path — they cannot access anything outside it. Tool
  arguments that try (`../`, absolute paths) get rejected client-side
  before they reach the server.
- Writing outside the workspace happens only via a hal0-admin MCP
  tool (e.g., `model_store_write`, `config_write`) that already
  goes through ADR-0004's approval queue.

### 6. Bootstrap path (Hermes-Agent, v0.3)

- `installer/agents/hermes.sh` installs the agent + drops a default
  `/etc/hal0/agents/hermes.toml` containing:
  - `hal0-admin` + `hal0-memory` (builtin).
  - One external MCP (filesystem, scoped to the workspace) — proof
    of stream #5's *"at least one MCP-client external source
    connectable from a bundled agent"* ship criterion.
- Hermes bootstrap state machine (per `hermes-bootstrap-plan-2026-05-23.md`)
  reads this file in the `mcp_wire` phase, registers the connections
  with Hermes's MCP client, and proceeds.
- A failure to connect to a non-builtin MCP logs + continues — the
  agent doesn't fail bootstrap on a stale URL or missing token.

### 7. Approval-queue integration

- Calls to `tools.gated` enqueue exactly like ADR-0004's hal0-admin
  destructive-tool gating: dashboard bell + inline pending indicator
  + CLI parity via `hal0 agent approvals {list,approve,deny}`.
- The approval record carries `{agent_name, mcp_server, tool, args,
  client_id}` — same envelope as hal0-admin approvals so the dashboard
  doesn't need a parallel UI.

### 8. Dashboard surface (v0.3 follow-up; not blocking ADR)

- v3's `/agents/mcp` page gains a **per-agent view** (sibling to the
  existing per-server view).
- Read-only in v0.3 alpha; editable in v0.3 stable.
- Edit writes back to `/etc/hal0/agents/<name>.toml` atomically (same
  envelope writer as `/etc/hal0/slots/*.toml`).

## Consequences

### Positive

- Cleanly resolves PLAN.md §1 v0.3 ships-when item without expanding
  ADR-0011's scope into configuration.
- Default-deny on both server + tool axis matches the structurally
  correct security posture without forcing users to think about it
  unless they want to extend.
- Per-agent file mirrors the slot/upstream layout users already
  understand — no new mental model.
- Installer-pinned blocks survive user edits without locking out
  expert users (TOML edit always wins, just loudly).

### Negative / costs

- Three classifications (allow/gated/blocked) is one more than a
  pure two-tier system. Mitigation: the dashboard renders the
  classification with a clear three-color chip — one decision per
  tool, one click to migrate.
- Per-agent files multiply small files in `/etc/hal0/`. Mitigation:
  the existing slots layout already does this; tooling exists.
- External MCP availability is bursty — servers can disappear, tokens
  rot. Mitigation: dashboard surfaces per-server health (green/yellow/
  red dot), and `hal0 agent doctor <name>` round-trips every allowed
  server during bootstrap repair.
- The `auth.kind = "bearer-from-env"` indirection requires care so
  tokens don't leak into systemd unit dumps. Mitigation: tokens loaded
  at agent-process startup from systemd-credential or env file with
  `0600 hal0:hal0` perms — never in the agent's command line.

## Pending items

- `src/hal0/config/schema.py` — `AgentConfig`, `MCPServerConfig`,
  `ToolPolicy` pydantic models.
- `src/hal0/agents/mcp_client.py` — the per-agent MCP client surface
  that reads the config + enforces the three-tier classification.
- `installer/agents/hermes.sh` — adds the default
  `/etc/hal0/agents/hermes.toml` writer.
- Dashboard `/agents/mcp` per-agent view (v0.3 follow-up issue, to be
  filed).
- Docs page `docs/agents/mcp-client.md` written before the dashboard
  surface ships.
- Cross-link from ADR-0011 (identity cards) to this ADR: the
  identity card's `allowed_tools` projection is *derived from* the
  allow-list defined here; it's not the source of truth.
