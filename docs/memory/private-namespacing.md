---
title: Per-agent private memory
description: The private:<agent_id> dataset gives each agent an isolated working memory namespace. The target shape is defined; the runtime is currently degraded — see issue #317.
sidebar:
  order: 3
---

Per [ADR-0005 §3](../../internal/adr/0005-memory-engine-cognee.md),
hal0-memory supports a **per-agent private namespace** —
`private:<client_id>` — so each agent has an isolated working-memory
store while still sharing the same Cognee engine, the same on-disk
location, and the same tool surface.

This page documents the **target shape**, then calls out the gap on
`main`.

## Target shape

Every agent's identity card (in the `agents` dataset, per
[ADR-0011](../../internal/adr/0011-agent-identity-cards.md)) declares
its working-memory namespace:

```json
{
  "dataset": "agents",
  "metadata": {
    "agent_id": "hermes-agent",
    "namespace": "private:hermes-agent",
    ...
  }
}
```

The convention is `private:<agent_id>` — colon-separated, agent id
matches the card's `agent_id`. Memory inside the namespace is
**not** routed differently — same SQLite, same LanceDB index,
filtered by the `dataset` column at query time.

### How an agent writes to its private namespace

Two ways, both supported by the schema today:

1. **Header opt-in** — set `X-hal0-Private: 1` on the MCP / REST
   request. The server-side `_resolve_dataset` helper
   (`src/hal0/mcp/memory.py:134`) promotes the write to
   `private:<client_id>` automatically, ignoring any explicit
   `dataset` arg.
2. **Explicit dataset arg** — pass
   `dataset="private:<agent_id>"` directly. Useful for scripts that
   want to address a specific agent's namespace from outside.

The header form is what bundled agents use; the explicit form is
what scripts + admin tools use.

### How searches scope to private memory

`memory_search` accepts `dataset` as either a string or a list of
strings (per `src/hal0/mcp/memory.py:220` and ADR-0005 §2). An agent
typically searches its own private namespace plus `shared`:

```jsonc
{
  "tool": "memory_search",
  "args": {
    "query": "what did the user say about the dock NVMe last week",
    "dataset": ["private:hermes-agent", "shared"]
  }
}
```

Results carry the `dataset` field so the caller can tell which scope
each hit came from.

### Lifecycle

- **Created**: implicitly on first write — Cognee doesn't require
  declaration. The agent's bootstrap doesn't pre-create the namespace;
  the first `memory_add` does.
- **Cleared on uninstall**: `hal0 agent uninstall <name>` tears
  down the agent's private namespace **and** its identity card
  unless `--keep-memory` is passed. Source:
  `src/hal0/cli/agent_commands.py:161` (`_uninstall_hermes_memory`).
- **Reused on re-install**: with `--keep-memory`, the namespace
  survives. Re-install + re-bootstrap rewires the agent with the
  same `client_id` and picks up the same private memory.

### Identity sourcing

The `client_id` substituted into `private:<client_id>` is the
identity the MCP transport stamps on the request. Per ADR-0012, the
intended source is the `X-hal0-Agent` request header (mirroring the
`HAL0_AGENT_ID` env the wrapper exports). The server-side wire to
read that header is a deferred follow-up — see the caution block
below, and [`agents/identity.md`](../agents/identity.md) for the
full identity gap.

:::caution[🚧 TO BE DOCUMENTED]
This section is intentionally a placeholder. The underlying surface is not yet
wired end-to-end on `main`:

- **Server-side X-hal0-Agent read**: clients send the `X-hal0-Agent` header
  (set from `HAL0_AGENT_ID` env), but `src/hal0/api/mcp_mount.py` still
  resolves `client_id` from `Authorization: Bearer` and falls back to
  `"anonymous"`. The `MCPAuthMiddleware → MCPIdentityMiddleware` rename
  + server-side header read are deferred follow-ups to ADR-0012.
- **Per-agent private memory**: issue #317 — `/api/memory/add` and the
  hal0-memory MCP server force `dataset` to `"shared"` regardless of the
  incoming `private:<agent_id>` namespace. Per-agent private memory is
  currently a no-op.

This page will be completed once both land (target: v0.3.0-alpha.2).
:::

## What works today

Even with the two halves of the wire unfinished:

- The **schema** is correct — `_resolve_dataset` in
  `src/hal0/mcp/memory.py` already implements the promotion rule, and
  the REST shims forward `dataset` to the Cognee wrapper.
- **Identity cards** in the `agents` dataset still write + discover
  correctly (the bug is on the write path of *episodic* items into
  `private:*`, not on the `agents` dataset).
- **Explicit `dataset=` reads work** — a caller that passes
  `dataset="private:hermes-agent"` and there happens to be data
  stamped that way (e.g., from a test seed) will see it. The bug is
  on the *write* path normalising new items to `shared`.

What does **not** work yet:

- The `X-hal0-Private: 1` header alone produces `shared`-bucket
  writes.
- Two agents on the same host **share** episodic memory unintentionally
  because both write into `shared`.
- The Hermes bootstrap `memory_roundtrip` smoke test stays red for
  this reason — see [issue #317](https://github.com/Hal0ai/hal0/issues/317)'s
  "Why it matters" section.

## See also

- [ADR-0005 — Memory engine = Cognee](../../internal/adr/0005-memory-engine-cognee.md) — §3 namespace rule.
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md) — where each agent declares its namespace.
- [ADR-0012 — Remove auth and Caddy entirely](../../internal/adr/0012-remove-auth-and-caddy.md) — identity sourcing rationale.
- [Issue #317 — `memory_add` normalizes dataset to "shared"](https://github.com/Hal0ai/hal0/issues/317) — the open bug.
- [`agents/identity.md`](../agents/identity.md) — the agents-side half of the same gap.
- [`overview.md`](./overview.md) — dataset model + data location.
