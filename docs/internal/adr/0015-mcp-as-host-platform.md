# ADR 0015 — hal0 is an MCP host platform (v0.3)

- **Status:** Draft (target: `v0.3.0-alpha.2`)
- **Date:** 2026-05-27
- **Drivers:** `hal0_mcp_host_platform` auto-memory note —
  "`/agents` MCP view should host arbitrary aftermarket MCP servers
  (catalog + install + supervise), not just bundled hal0-admin /
  hal0-memory. Treat like capability slots." Issue **#224** —
  open; the dashboard `/agents/mcp` page's install-from-URL UI is
  a placeholder.
- **Related:** ADR-0011 (agent identity cards), ADR-0012 (remove
  auth and Caddy entirely), ADR-0013 (MCP-client allow-list for
  bundled agents), ADR-0017 (bell+inbox approval UX).

## Context

hal0 today mounts two MCP servers in-process under the FastAPI app:

- `hal0-admin` at `/mcp/admin` — wraps existing `/api/*` routes
  (per ADR-0004 §4) so a bundled agent can drive hal0's slot /
  model / capability surface.
- `hal0-memory` at `/mcp/memory` — wraps Cognee for episodic
  memory + agent identity cards (per ADR-0005 + ADR-0011).

Both are built by FastMCP and mounted in
`src/hal0/api/mcp_mount.py::mount_mcp_servers`. They share one
identity middleware (`MCPAuthMiddleware`, to be renamed to
`MCPIdentityMiddleware` per ADR-0012) and one in-process dispatch
path. ADR-0013 has already locked in the *client side* of this story
for bundled agents: each agent declares which MCP servers it can
reach and which tools it can call, in
`/etc/hal0/agents/<name>.toml`, with default-deny on both axes.

What is **not** yet decided is the **host side** for *external* MCP
servers. The patterns users want to support:

1. A user finds a useful third-party MCP server upstream (filesystem,
   github, brave-search, a custom corp knowledge MCP, etc.) and
   wants to install it on their hal0 host so any of their agents can
   reach it.
2. A user wants to write a one-off MCP server (a few tools wrapping
   their own scripts) and run it under hal0's supervision instead of
   hand-rolling a systemd unit.
3. The dashboard `/agents/mcp` page currently lets the user view
   which MCP servers exist but cannot actually install or manage
   them. Issue #224 tracks the placeholder install-from-URL UI.

This ADR is the gap-closer: it declares hal0 a **first-class MCP
host platform**, and it treats third-party MCP servers as
*structurally equivalent* to the bundled `hal0-admin` and
`hal0-memory` — same lifecycle states, same dashboard surface, same
supervision model. The capability-slots system (`hal0_capability_slots_system`
in auto-memory) is the prior art: bundle catalog +
`/etc/hal0/` registry + systemd template + dashboard tile.

ADR-0013 is the dual side of this: ADR-0015 says *which MCP servers
can run on the host at all*, ADR-0013 says *which of those each
bundled agent is allowed to call*.

## Options considered

| Option | Reason rejected (or accepted) |
|---|---|
| **In-process FastAPI mounts only** (today's shape, only for first-party MCPs) | Rejected as the target end state. Works for hal0's two own MCPs but forces every third-party MCP to be Python + importable by `hal0-api`. Excludes stdio MCPs entirely, excludes anything not packaged for in-process import. |
| **stdio MCP per agent, agent supervises** (each bundled agent spawns its own stdio MCP children) | Rejected. Forces each agent to be a process supervisor. Lifecycle, restart, logging, port assignment all duplicated per agent. Bundle-fatigue. |
| **External MCP server registry under systemd template `hal0-mcp@<name>.service`** + `[mcp.servers]` registry in `/etc/hal0/` | **ACCEPTED.** Mirrors the slot lifecycle pattern users already understand (`hal0-slot@.service`, `/etc/hal0/slots/*.toml`). One supervision model across slots + MCP servers. Dashboard reuse. |
| **One monolithic MCP gateway** (route external MCPs through one in-process aggregator) | Rejected for v0.3. The MCP spec already supports per-server routing on the client side (ADR-0013's allow-list); putting an aggregator in front adds latency, complicates auth-passthrough, and conflicts with FastMCP's session semantics. Could be revisited in v1.0 if discovery scaling becomes a problem. |
| **Container-only MCP hosting** (require Docker for every external MCP) | Rejected for v0.3. Many useful MCPs are 50-line stdio scripts; mandating containerisation kills the on-ramp. Containers remain an option (Docker/Podman launch wrapped by the systemd unit) but not a requirement. |

## Decision

### 1. Two source classes of MCP server

hal0 hosts MCP servers from two sources:

| Class | Examples | Lifecycle owner |
|---|---|---|
| **Bundled** | `hal0-admin`, `hal0-memory` | hal0-api (in-process FastMCP mount) |
| **External** | User-installed third-party MCPs from the curated allow-list or a user-added URL | systemd template `hal0-mcp@<name>.service` |

Bundled MCPs stay mounted in-process for the obvious reasons:
- They are written against hal0's own dispatcher and Cognee handle.
- Round-trip latency matters for `memory_*` tools called inside an
  agent's hot loop.
- Their lifecycle is `hal0-api`'s lifecycle by definition.

External MCPs run under their own systemd unit so:
- They can be written in any language (stdio MCPs are mostly
  Node/Python today).
- A crash in a third-party MCP doesn't take down `hal0-api`.
- Restart / log / supervise semantics match what users already know
  from slots.

### 2. Registry at `/etc/hal0/mcp/servers/<name>.toml`

One file per external MCP server. Mirrors `/etc/hal0/slots/*.toml`
exactly. Sketch:

```toml
# /etc/hal0/mcp/servers/filesystem.toml
schema_version = 1

[server]
name        = "filesystem"
display     = "Filesystem MCP"
source      = "registry:filesystem@1.0.2"   # or "url:https://..." or "git:...#sha"
transport   = "stdio"                       # "stdio" | "streamable-http" | "sse"
enabled     = true

[server.runtime]
# What systemd actually exec's. Validated against the curated allow-list
# for source=registry; honour-trust-but-warn for user-added sources.
command     = "npx"
args        = ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
env.HOME    = "/var/lib/hal0/mcp/filesystem"

[server.health]
# Polled by hal0-api to update lifecycle state.
probe       = "tool_list"      # "tool_list" | "ping" | "none"
interval_s  = 30
```

A `[mcp.servers.*]` section in an agent's
`/etc/hal0/agents/<name>.toml` (ADR-0013) references one of these by
`name`. If the agent allow-list names a server that has no
`/etc/hal0/mcp/servers/<name>.toml`, the agent gets a
`mcp.server_not_installed` error at startup and the dashboard surfaces
the gap (with a one-click "Install" button if the name matches a
curated catalog entry).

### 3. Lifecycle states match slots

External MCP servers carry the same five lifecycle states as slots:

| State | Meaning |
|---|---|
| `CONFIGURED` | TOML exists, unit installed, not yet started. |
| `WARMING` | `systemctl start` issued; waiting for the first health probe to succeed. |
| `READY` | Health probe green; agents can dispatch. |
| `FAILED` | Health probe red AND systemd unit is in `failed` state, OR unit start exit code ≠ 0. |
| `OFFLINE` | `enabled = false` in TOML OR explicit user stop. |

Transitions:
- `CONFIGURED → WARMING → READY` — happy path on enable.
- `READY → OFFLINE` — user disabled or stopped.
- `* → FAILED` — health probe failure or systemd failure.
- `FAILED → WARMING` — manual restart via dashboard or
  `hal0 mcp restart <name>`.

The dashboard's `/agents/mcp` page renders these the same way
`/slots` renders slot lifecycle today — same chip colours, same
inline restart button.

### 4. Catalog

Curated allow-list ships in `installer/manifests/mcp-catalog.toml`
(new file; installer drops a copy to `/var/lib/hal0/mcp/catalog.toml`
on install, refreshable via `hal0 mcp catalog refresh`). The catalog
is the trust boundary for one-click install:

- Entries name an upstream `source` (`npm:`, `pypi:`, `git:`,
  `docker:`) with a version pin.
- Each entry carries a short description + default `[server.runtime]`
  block + recommended `[server.health]` block.
- One-click install in the dashboard reads from this catalog;
  user-added sources (URL or git ref outside the catalog) get a
  "user-trusted, not curated" badge in the UI and a confirm prompt.

### 5. CLI surface

```sh
hal0 mcp catalog list                    # what's in the curated catalog
hal0 mcp install <name>                  # from catalog
hal0 mcp install --url <url> <name>      # user-trusted
hal0 mcp list                            # what's installed locally
hal0 mcp status <name>                   # lifecycle state + last health probe
hal0 mcp restart <name>
hal0 mcp uninstall <name>
hal0 mcp catalog refresh                 # pull a fresh curated catalog
```

The `install-from-URL` path is the implementation of issue #224's
placeholder; this ADR is the design doc for it.

### 6. systemd template

`/etc/systemd/system/hal0-mcp@.service`:

```ini
[Unit]
Description=hal0 MCP server (%i)
After=network.target hal0-api.service
PartOf=hal0-api.service

[Service]
Type=simple
User=hal0
Group=hal0
WorkingDirectory=/var/lib/hal0/mcp/%i
EnvironmentFile=-/etc/hal0/mcp/servers/%i.env
ExecStart=/usr/lib/hal0/mcp/runner %i      # reads /etc/hal0/mcp/servers/%i.toml, exec()s [server.runtime]
Restart=on-failure
RestartSec=2s
```

The runner shim (one Python script) reads the TOML, applies env,
exec()s the configured command. Same model as the slot template.

### 7. Approval-gating contract (cross-reference to ADR-0017)

External MCP server tools inherit the same destructive-by-default
classification as bundled-agent MCP tools. The bell + inbox UI
shipped via Epic #322 (PRs #321 / #328 / #329 / #330 / #332) gates
all calls flagged DESTRUCTIVE via the MCP `annotations` block in
the tool's schema, regardless of which MCP server hosts the tool.
See ADR-0017 for the classification rule.

This means a third-party MCP that ships a `delete_*` tool **without**
DESTRUCTIVE annotation still gates by default (unclassified =
DESTRUCTIVE — see ADR-0017 §3). Catalog-curated entries can override
in `/etc/hal0/mcp/servers/<name>.toml` if the tool genuinely is
side-effect-free.

### 8. Auth posture

Per ADR-0012, hal0 has no built-in auth. External MCP servers
inherit this: the supervised process binds to localhost (or to a
Unix socket — preferred where the transport supports it) and is
reachable only to local processes. Operator-grade auth is the
reverse proxy's job; the MCP server itself does not authenticate.

For external MCPs that need outbound credentials (e.g., a GitHub
MCP needing a token), the `auth.kind = "bearer-from-env"` pattern
from ADR-0013 §2 is reused: tokens live in
`/etc/hal0/mcp/servers/<name>.env` with `0600 hal0:hal0` perms, are
loaded into the unit's environment by `EnvironmentFile=`, and are
never surfaced to the agent (the MCP server reads them; the agent
only sees tool calls + responses).

## Consequences

### Positive

- Closes the gap identified in `hal0_mcp_host_platform` auto-memory
  with a structurally consistent answer (slots model, applied to
  MCPs).
- Closes issue #224 with a concrete design instead of a placeholder.
- One supervision model across slots, capabilities, MCP servers,
  and agents — the operator learns one mental model.
- ADR-0013's per-agent allow-list now has a real "what's available
  to allow" set (the installed MCP servers list).
- Third-party MCPs can be written in any language without forcing
  Python-in-process integration.
- Dashboard surface `/agents/mcp` becomes coherent: per-server tiles
  with lifecycle chips, install button, log link, restart button.

### Negative / costs

- One more registry directory to back up (`/etc/hal0/mcp/`).
  Mitigation: the installer already includes `/etc/hal0/**` in its
  config-preservation envelope.
- The curated MCP catalog is an editorial commitment — we own
  vetting upstream MCPs before listing them. Mitigation: keep the
  v0.3 catalog small (filesystem, git, brave-search, fetch — a
  handful of high-confidence MCPs) and grow only on demand.
- A misbehaving external MCP can hold a port, leak memory, or stall
  health probes. Mitigation: `Restart=on-failure` + the health
  probe → `FAILED` state transition surfaces the breakage in the
  dashboard; operator action.
- Catalog refresh introduces a network dependency. Mitigation: it's
  pull-only (`hal0 mcp catalog refresh`), never auto on boot;
  failed refresh logs WARN and keeps the prior catalog.
- One-click install + arbitrary URLs are an attack surface. The
  "user-trusted" confirm gate is the user's contract; we explicitly
  do *not* claim safety guarantees for URL-installed MCPs.

### Neutral

- Bundled MCPs (`hal0-admin`, `hal0-memory`) stay in-process; this
  ADR doesn't propose moving them. They are documented in this same
  surface for symmetry (the dashboard shows them as "bundled,
  in-process" tiles), but their lifecycle is `hal0-api`'s.

## Pending items

- `installer/manifests/mcp-catalog.toml` — initial catalog file.
- `src/hal0/mcp/runner.py` (or similar) — the systemd ExecStart shim.
- `src/hal0/mcp/registry.py` — TOML parser + lifecycle state machine
  + health probe driver.
- `src/hal0/api/routes/mcp_servers.py` — `/api/mcp/servers/*` CRUD +
  lifecycle endpoints feeding the dashboard.
- `src/hal0/cli/mcp_commands.py` — `hal0 mcp ...` subcommands.
- Dashboard `/agents/mcp` page — replace the placeholder install
  UI with the catalog + URL flow; per-server tiles.
- Documentation page `docs/mcp/host-platform.md` describing the
  shape for users (PR-3's scope).
- New GH issue (to be filed during implementation): track the
  follow-up "v3 dashboard MCP page wired to /api/mcp/servers/*"
  work and link this ADR.

## Open questions

- Should MCP server health probes share the slot health-probe
  scheduler or run their own loop? Initial guess: share, because the
  cadence is the same and one less thread is cheaper.
- Should the dashboard treat bundled MCPs (`hal0-admin`,
  `hal0-memory`) as configurable at all, or render them as read-only
  "always-on" tiles? Leaning read-only — they're tied to
  `hal0-api`'s lifecycle, so a per-MCP enable toggle would be
  misleading.
- How does a future federation story (multi-host hal0) discover
  external MCPs on peer hosts? Out of scope for v0.3; the
  `agents` dataset (ADR-0011) is the analogous discovery
  substrate for agents and a similar pattern could extend to MCPs.

## References

- ADR-0011 — agent identity cards (the `agents` dataset is the
  prior art for "dedicated dataset for service-registry use").
- ADR-0012 — auth removal (sets the auth posture inherited here).
- ADR-0013 — MCP-client allow-list for bundled agents (the dual
  side of this ADR).
- ADR-0017 — bell + inbox approval UX for destructive MCP calls
  (the gating contract that extends to third-party MCP servers).
- `hal0_mcp_host_platform` auto-memory — the gap-statement this ADR
  closes.
- `hal0_capability_slots_system` auto-memory — prior art for the
  catalog + systemd template + dashboard tile pattern.
- Issue #224 — dashboard `/agents/mcp` install-from-URL placeholder.
- `src/hal0/api/mcp_mount.py` — current in-process mount path for
  bundled MCPs.
