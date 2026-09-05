# ADR-0004: Bundled agents — bundle third-party runtimes, don't build one

## Status

**ACCEPTED.** First shipped as Phase 8 (v0.2, two candidate runtimes:
`pi-coder` and `Hermes-Agent`); reconstructed here against the current
(v0.3+) state. The original write-up lived in the gitignored
`docs/internal/adr/0004-agents.md` (see `ARCHITECTURE.md` "Decision
records"); this file restates the decision as it stands today, not the
v0.2 draft.

## Context

hal0 previously carried a first-party agent runtime (`haloai`) that was
stripped from the codebase. Re-entering the "an agent that chats and
takes action" space needed a rule that kept that strip line honest: hal0
does not get to quietly rebuild what it just deleted.

## Decision

**hal0 bundles third-party agent runtimes; it does not build its own.**
Concretely:

- `BUNDLED_AGENTS` (`src/hal0/agents/manager.py:117`) is the closed list
  of agents hal0 knows how to install: `("hermes", "pi")`. Adding a new
  one is a driver module + a `BUNDLED_AGENTS` entry, not new runtime
  code.
- **Single-pick, but only between daemon-kind agents**
  (`src/hal0/agents/manager.py:294-335`, spec D1). Hermes is a
  service-shaped, systemd-supervised daemon; a `cli`-kind agent (`pi`)
  installs alongside anything, since it has no gateway/persona surface
  to collide over. `hal0 agent install <name> --switch` performs an
  atomic uninstall-then-install between daemon agents so the operator
  never ends up with two partially installed.
- **Sandboxed sibling unit, not an in-process extension.** A daemon
  agent runs as `hal0-agent@<id>.service`
  (`installer/systemd/hal0-agent@.service`), `User=hal0`, loopback-only,
  `NoNewPrivileges`/`ProtectSystem=strict`/`ProtectHome=yes`. The browser
  never talks to it directly — `src/hal0/api/agents/chat_proxy.py`
  proxies the chat WebSocket, enforcing an Origin allowlist and a session
  cookie, carrying the embed token server-side only.
- **One MCP tool catalog, mapped onto existing routes.** The admin MCP
  server's tool catalog (`src/hal0/mcp/admin.py`) started as the "a tool
  ships iff it maps to an existing `/api/*` route" rule; no privileged
  surface is invented just for the agent (`src/hal0/mcp/admin.py`,
  "Platform-management expansion" section header cites this ADR by
  number). Tools are split autonomous-read / autonomous-write / gated,
  the same two-tier trust model this ADR established.
- **Gated destructive actions go through an approval inbox, not a trust
  toggle.** `GET/POST /api/agent/approvals[/{id}/approve|deny]`
  (`src/hal0/api/routes/approvals.py`) backs the dashboard's approvals
  bell and the `hal0 agent approvals` CLI off one
  `hal0.mcp.approval_queue.ApprovalQueue`. There is deliberately no
  per-agent "trust this agent, skip approval" setting — untrusted content
  reaching the agent's context must still clear a human click for a
  destructive call.
- **Shim-first integration, native where upstream cooperates.** hal0 owns
  a thin wrapper per agent (e.g. `/usr/local/bin/hal0-hermes`) that wires
  hal0 in as the agent's local AI provider and MCP client, rather than
  forking or patching the upstream agent.

## Consequences

- v0.2/v0.3 shipped as a wiring job, not a multi-month runtime-engineering
  project; MCP is the cross-app contract, so other MCP clients (not just
  bundled agents) can drive the same admin surface.
- The tool-catalog rule keeps the agent's privileges from silently
  outgrowing what the dashboard itself can do — a new admin capability
  needs a `/api/*` route first, an MCP tool second.
- Power users wanting two daemon agents installed at once are blocked by
  single-pick; the escape hatch is installing a second agent through its
  own upstream path, forgoing hal0's prewiring.
- The specific agent lineup has changed since v0.2 (`pi-coder` →
  `pi`, `Hermes-Agent` → `hermes` as the default); this ADR documents the
  standing rule, not the roster, so it does not need an amendment every
  time a bundled agent is added or renamed — see `BUNDLED_AGENTS` for the
  current roster.

## References

- `src/hal0/agents/manager.py:1-38,117,294-338` — module contract,
  `BUNDLED_AGENTS`, single-pick enforcement
- `installer/systemd/hal0-agent@.service` — sandboxed daemon unit
- `src/hal0/api/agents/chat_proxy.py` — WS proxy, Origin allowlist
- `src/hal0/mcp/admin.py` — tool catalog, "Platform-management expansion"
  section citing this ADR
- `src/hal0/api/routes/approvals.py` — approval inbox routes
- `ARCHITECTURE.md` "Bundled agents (v0.3)" — current process model,
  surfaces, and module map
- `docs/concepts/agents.mdx` — operator-facing description
