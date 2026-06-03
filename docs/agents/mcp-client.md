---
title: Agent MCP client allow-list
description: Per-agent allow-list at /etc/hal0/agents/<name>.toml — server-axis and tool-axis default-deny, three-tier classification (allow/gated/blocked), filesystem sandbox.
sidebar:
  order: 4
---

Bundled agents (Hermes-Agent in v0.3) are MCP **clients** as well as
MCP servers: they reach out to external MCP servers (filesystem,
GitHub, search, custom user-installed) to read context or take
actions. ADR-0013 governs **what they're allowed to reach** with a
per-agent allow-list config + a three-tier classification for
individual tools.

See [ADR-0013](../../internal/adr/0013-mcp-client-allow-list.md) for
the full rationale.

## TL;DR

- One TOML per agent: `/etc/hal0/agents/<name>.toml`.
- Server-axis **default-deny**: a server not listed is unreachable.
- Tool-axis **default-deny**: a tool not on `allow` / `gated` is
  rejected client-side.
- `tools.gated` calls go through the [ADR-0004 approval queue](./overview.md#approvals-gated-tools).
- `tools.blocked` is a hard reject the dashboard can't undo
  (installer-pinned blocks survive user edits; TOML edit always wins,
  loudly).
- Outbound bearer tokens (e.g. for github-mcp) live in env, never in
  TOML — `auth.kind = "bearer-from-env"` points at an env var the
  agent driver loads at process startup. (hal0 itself has no inbound
  auth per ADR-0012 — this is strictly outbound.)

## Schema

```toml
# /etc/hal0/agents/hermes.toml
schema_version = 1

[agent]
name        = "hermes"
display     = "Hermes-Agent"
# Filesystem sandbox. Agent process sees only this path as its workspace.
workspace   = "/var/lib/hal0/.hermes/workspace"

# ---------------------------------------------------------------
# MCP servers the agent may connect to.
# Default-deny: an unlisted server is unreachable.
# ---------------------------------------------------------------

[mcp.servers.hal0-admin]
builtin = true        # Always allowed for bundled agents.

[mcp.servers.hal0-memory]
builtin = true

[mcp.servers.filesystem]
url      = "stdio:///usr/lib/hal0/mcp/filesystem-server"
enabled  = true
tools.allow = [
    "read_file",
    "list_directory",
    "search_files",
]
tools.gated = [
    "write_file",           # enqueues approval per ADR-0004
]

[mcp.servers.github]
url       = "https://api.github.com/mcp"
enabled   = false           # opt-in
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
tools.blocked = [
    "delete_repo",          # installer-pinned; dashboard can't undo
    "delete_branch",
]
```

Schema source + validator: PR #293 (`feat(agents): ADR-0013
MCP-client allow-list schema + mcp_client.py`).

## Three-tier classification

| Tier              | What happens at call time              | Approval queue? |
|-------------------|-----------------------------------------|-----------------|
| `tools.allow`     | Autonomous call                         | No              |
| `tools.gated`     | Enqueued; user picks approve/deny       | Yes (ADR-0004)  |
| `tools.blocked`   | Hard reject at the client; never wires  | N/A             |

The lists **must be disjoint** — overlap is a load-time
`ValidationError` with the offending tool name in the message.

A tool that doesn't appear on any list is **default-denied** with the
verdict `unknown_tool` — same behavior as `blocked`, just a distinct
audit-log marker so the dashboard can tell you "you didn't add this
tool to allow yet".

### Migrating a tool between tiers

- `gated → allow`: trust earned, autonomous from now on.
- `allow → gated`: trust eroded, force approval per call.
- Anything → `blocked`: hard ban. Reversible by editing the TOML.

Installer-pinned `blocked` entries (e.g. `delete_repo` on github-mcp)
survive **dashboard edits** but not direct TOML edits. The TOML is
the source of truth and we want operators who edit it to *know*
they're loosening a guardrail.

## Filesystem sandbox

`workspace` defaults to `/var/lib/hal0/agents/<name>/workspace`. The
agent driver:

1. Sets that path as the agent process's HOME + CWD.
2. Pins filesystem MCPs' server-side root to the same path.
3. Rewrites tool arguments client-side — `../` and absolute paths
   outside the workspace get rejected with `WorkspaceEscapeError`
   **before** they reach the server.

Writing outside the workspace goes through a hal0-admin MCP tool
(`model_store_write`, `config_write`) that itself uses the ADR-0004
approval queue.

## Approval-queue integration

When the agent calls a `tools.gated` tool, the request enqueues with
the same envelope as a hal0-admin destructive-tool call:

```json
{
  "agent_name":  "hermes",
  "mcp_server":  "filesystem",
  "tool":        "write_file",
  "args":        {"path": "notes.md", "content": "..."},
  "client_id":   "hermes-process-12345"
}
```

The dashboard bell + the `hal0 agent approvals` CLI both render gated
calls in the same inbox — operators don't need to learn two surfaces.
CLI: `hal0 agent approvals {list,approve,deny}`
(`src/hal0/cli/agent_commands.py:373`).

## Outbound auth (tokens)

`auth.kind` is currently:

- `none` — no auth header sent. Default.
- `bearer-from-env` — agent driver reads `auth.env` at process
  startup, sends `Authorization: Bearer <value>` on every request.

Tokens are loaded from systemd-credential (`LoadCredential=` in the
unit file) or a `0600`-permission env file. **They never appear on
the command line** — `ps auxe` is a search target on a compromised
box and we leak nothing useful via that surface.

Missing env vars at startup log a warning + skip the server
gracefully. ADR-0013 §6 picks "log + continue" over "fail bootstrap"
because a single stale token shouldn't block the agent.

## Dashboard view

PR #300 added a read-only **Clients** mode to `/agents/mcp` alongside
the existing **Servers** view. One card per agent shows the four-tuple
per allowed server:

- Server name + health dot + `builtin` / `disabled` chip.
- Auth chip (`bearer-from-env` status only, never the token value).
- Tool chips coloured by classification — green `allow`, amber
  `gated`, red `blocked`, grey `unknown_tool`.

The view is read-only in v0.3 — TOML edits are still the way to
change the allow-list. A future PR will surface inline edit + save.

## `hal0 agent doctor <name>` (planned)

Round-trips every allowed server during bootstrap repair. Output
shows per-server health (green / yellow / red) so operators can see
at a glance which entries in the TOML are still talking to a
reachable upstream. Currently planned; the MCP-wire phase of
`hal0 agent bootstrap hermes` performs an equivalent round-trip
synchronously (see [`hermes-bootstrap.md`](./hermes-bootstrap.md)
phase 6).

## See also

- [ADR-0013 — MCP-client allow-list](../../internal/adr/0013-mcp-client-allow-list.md)
- [ADR-0004 — Agents](../../internal/adr/0004-agents.md)
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md) — the `allowed_tools` projection is *derived from* this allow-list, not the source of truth.
- [ADR-0012 — Remove auth and Caddy entirely](../../internal/adr/0012-remove-auth-and-caddy.md) — context for why outbound `bearer-from-env` is the only bearer concept left in hal0.
