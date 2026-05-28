---
title: Agents in hal0
description: Bundled third-party agent apps that run on top of hal0. v0.3 ships Hermes-Agent only. pi-coder code stays in repo but is dropped from the v0.3 picker.
sidebar:
  order: 1
---

An "agent" in hal0 is a **bundled third-party agent app** that runs on
top of hal0 and treats it as the local AI provider. The hal0 shim does
*install + wire only* — it never authors prompts, never dispatches
tools, never owns the agent's process loop. Upstream owns the agent;
hal0 owns the seam that makes the agent hal0-aware.

This is "bundle, don't build" per [ADR-0004 §1](../../internal/adr/0004-agents.md#1-bundle-dont-build).
The term is overloaded inside hal0 — see `CONTEXT.md` — but on this
page it always means the bundled-app sense, never "slot",
"capability child", or "MCP client in general".

## What v0.3 ships

| Agent           | Status in v0.3        | Shape   | Where to start |
|-----------------|-----------------------|---------|----------------|
| Hermes-Agent    | Bundled, picker-visible | Service (`hal0-agent@hermes.service`) | [`hermes-bootstrap.md`](./hermes-bootstrap.md) |
| pi-coder        | Code in repo, **dropped from picker + promo** | CLI | n/a in v0.3 |

pi-coder code stays at `installer/agents/pi-coder.sh` +
`src/hal0/agents/` for reactivation after the Lemonade UI overhaul.
v0.3 narrows the user-facing bundle to Hermes only — see the
release-notes for the rationale.

## Pick + lifecycle

`hal0 agent install <name>` is the single entrypoint. It is **interactive
opt-in only** — `install.sh` does not take an `--agent` flag (the
non-interactive promise from ADR-0001's install path still holds, even
post-ADR-0012).

```sh
# Install the bundled Hermes-Agent.
hal0 agent install hermes

# Switch from one bundled agent to another atomically
# (uninstall-then-install; never leaves two partially installed).
hal0 agent install hermes --switch

# List what's installed.
hal0 agent list

# Uninstall (default tears down agent + private memory namespace + identity card).
hal0 agent uninstall hermes

# Keep the agent's private memory + identity card on uninstall
# so a future re-install picks up where it left off.
hal0 agent uninstall hermes --keep-memory
```

Source: `src/hal0/cli/agent_commands.py:50` (install), `:81` (uninstall),
`:283` (list). The REST surface is at `src/hal0/api/routes/agents.py`.

Single-pick is enforced per ADR-0004 §2 — installing a second bundled
agent without `--switch` exits non-zero. The CLI surfaces the
already-installed name in the error.

## Runtime shape (asymmetric on purpose)

Hermes-Agent is a **long-running service**. It runs as
`hal0-agent@hermes.service`, an instance of the `hal0-agent@.service`
template that mirrors `hal0-slot@.service`. The dashboard surfaces it
as a sidebar link-out (OWUI-style — no in-dashboard embed).

pi-coder, when active, is a **CLI** — no systemd unit, no dashboard
surface. Users invoke it from a terminal.

ADR-0004 §3 deliberately refuses to flatten these two shapes into one
abstraction. Future bundled agents are expected to follow the upstream
shape, not the average.

## What "bootstrap" means

After `hal0 agent install hermes` lays down the binary + wrapper,
`hal0 agent bootstrap hermes` runs the 12-phase provisioning state
machine that makes Hermes actually hal0-aware: probes hardware,
enumerates models, claims a memory namespace, writes its identity
card, wires every relevant slot into the right Hermes subsystem.

The pipeline is checkpointed at
`/var/lib/hal0/state/agents/hermes/provision.json` and is idempotent —
re-running picks up at the first non-`ok` phase unless you pass
`--repair`.

See [`hermes-bootstrap.md`](./hermes-bootstrap.md) for the phase list
and operator commands.

## How agents reach external MCP servers

Bundled agents are MCP **clients** as well as MCP servers (they host
their own tool surface for upstream / peer consumption, *and* they
connect outbound to filesystem-MCPs, github-MCPs, custom user-added
MCPs). The outbound side is governed by a **per-agent allow-list**
per [ADR-0013](../../internal/adr/0013-mcp-client-allow-list.md).

See [`mcp-client.md`](./mcp-client.md) for the schema and the CLI.

## How agents announce themselves

When an agent finishes bootstrap, it writes an **identity card** into
the `agents` Cognee dataset. Peer agents discover it by searching that
dataset. Cards are immutable until re-bootstrap or uninstall.

See [`identity.md`](./identity.md) for the card shape +
[ADR-0011](../../internal/adr/0011-agent-identity-cards.md) for the
full schema.

## Approvals (gated tools)

Bundled agents call hal0-admin MCP tools to drive the host (slot ops,
model registry, config writes). Anything destructive — `model_pull`,
`slot_delete`, `config_write`, etc. — routes through the **approval
queue** (`src/hal0/mcp/approval_queue.py`). The queue is surfaced two
ways:

- Dashboard bell (the badge on `/agents`).
- CLI: `hal0 agent approvals {list,approve,deny}` —
  `src/hal0/cli/agent_commands.py:373` and following.

The classification (autonomous read / autonomous write / gated) is in
`src/hal0/mcp/admin.py:200` (`AUTONOMOUS_WRITE_TOOLS`) and `:215`
(`GATED_TOOLS`). `memory_delete` is the only tool whose gating
depends on args — single-id deletes are autonomous; bulk deletes
(>1 id) gate.

## Status, logs, upgrades

```sh
# Pretty-print the bootstrap checkpoint (every phase + outcome).
hal0 agent status hermes

# Tail a specific phase's log.
hal0 agent log hermes --phase mcp_wire

# Pin to a specific upstream version (otherwise tracks latest at install).
hal0 agent upgrade hermes --to 0.14.0
```

Source: `src/hal0/cli/agent_commands.py:559` (status), `:588` (log),
`:606` (upgrade).

## See also

- [ADR-0004 — Agents (Phase 8, v0.2)](../../internal/adr/0004-agents.md)
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md)
- [ADR-0013 — MCP-client allow-list](../../internal/adr/0013-mcp-client-allow-list.md)
- [`hermes-bootstrap.md`](./hermes-bootstrap.md) — what `hal0 agent bootstrap hermes` actually does.
- [`identity.md`](./identity.md) — identity-card schema + the header that derives `client_id`.
- [`mcp-client.md`](./mcp-client.md) — per-agent allow-list + sandbox.
