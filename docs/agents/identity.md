---
title: Agent identity
description: Per-ADR-0011, bundled agents write an immutable identity card into the agents Cognee dataset. The X-hal0-Agent header is the intended client_id source post-ADR-0012 — server-side wiring is a deferred follow-up.
sidebar:
  order: 3
---

Every bundled agent on a hal0 host gets an **identity card** —
an immutable, public-by-design memory item that says *"I am X, I do
Y, find me at Z"*. Peer agents discover what's running on the host by
searching for these cards.

This page documents the **target shape** per
[ADR-0011](../../internal/adr/0011-agent-identity-cards.md) and
[ADR-0012](../../internal/adr/0012-remove-auth-and-caddy.md), then
calls out the parts that are not yet wired end-to-end on `main`.

## Identity cards (ADR-0011)

When an agent finishes bootstrap, its `namespace_register` phase
writes one card into the `agents` Cognee dataset. The card is the
agent's **service-registry entry** — small, immutable, searchable by
similarity.

| Field | Storage | Purpose |
|-------|---------|---------|
| `dataset` | `"agents"` (literal) | Dedicated namespace — never `shared`, never `private:*`. |
| `tags` | `["agent-identity", "<agent_name>"]` | Discovery filter. |
| `text` | Short human summary | Surfaces well in semantic search ranking. |
| `metadata.agent_id` | Stable slug | Globally unique per host. Identifies the agent across the rest of hal0. |
| `metadata.display_name` | Human label | Dashboard + CLI listing. |
| `metadata.namespace` | `"private:<agent_id>"` | Where the agent's *working* memory lives. |
| `metadata.endpoint.{type,url,transport}` | URL + enum | If the agent is reachable; dashboard pings for liveness. |
| `metadata.roles` | `list[str]` | Capability rollup the agent self-asserts. |
| `metadata.delegation.{accepts_tasks_from,max_concurrent}` | List + int | Who can delegate; concurrency hint. |
| `metadata.hal0_state.{registered_at,hal0_version,bootstrap_version}` | iso8601 + str + int | Provenance. |

Full schema: [ADR-0011 §4](../../internal/adr/0011-agent-identity-cards.md#4-minimal-schema-required-fields).

### Why immutable

- Clean audit trail — every card has exactly one source-of-truth write
  event (the agent's bootstrap), one optional rewrite (re-bootstrap or
  version upgrade), and one delete (uninstall).
- Liveness is **not** a stored field. The dashboard pings
  `endpoint.url` for that — TTL-on-card invites concurrent-write bugs
  and "when's it stale" arguments.

### Discovery

```jsonc
{
  "tool": "memory_search",
  "args": {
    "query": "an agent that can do code review",
    "dataset": "agents",
    "tags": ["agent-identity"]
  }
}
```

Same query works against:
- `POST /api/memory/search` (REST shim per PR #303).
- `memory_search` MCP tool on `/mcp/memory`.
- Hermes's `Hal0MemoryProvider` plugin (in-process).

### Lifecycle

- Written: by `namespace_register` (phase 8 of
  [`hermes-bootstrap.md`](./hermes-bootstrap.md)).
- Rewritten: `hal0 agent bootstrap hermes --repair` or Hermes version
  upgrade (`hal0 agent upgrade hermes --to <ver>` followed by a
  re-bootstrap).
- Deleted: `hal0 agent uninstall hermes` calls
  `memory_delete(ids=[card_id])` as part of the teardown, unless
  `--keep-memory` is passed. Source:
  `src/hal0/cli/agent_commands.py:161` (`_uninstall_hermes_memory`).

## The `X-hal0-Agent` header (target shape)

Post-[ADR-0012](../../internal/adr/0012-remove-auth-and-caddy.md),
hal0 has **no inbound auth surface**. The bearer / password / OTP
plumbing was removed. In place of bearer-derived identity, the design
is:

- The hal0-managed wrapper (`installer/wrappers/hal0-hermes`) exports
  `HAL0_AGENT_ID=hermes-agent` into the agent's process env.
- The agent's MCP client passes that value as
  `X-hal0-Agent: hermes-agent` on every request.
- A server-side `MCPIdentityMiddleware` (renamed from
  `MCPAuthMiddleware`) reads the header and stamps `client_id` for
  the audit log, the `--private` namespace promotion, and any future
  identity-aware tooling.

That's the intent per ADR-0011 §"Identity sourcing update
(post-ADR-0012)" and ADR-0012's stream-4 follow-up. **One half of
this wire is not yet live on `main`** — see the caution block
below.

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

Until the server-side header read and #317 land:

- Identity-card writes to the `agents` dataset **work**. Cards land in
  the right dataset because the agent passes `dataset: "agents"`
  explicitly.
- Identity-card discovery via `memory_search` against `dataset:
  "agents"` **works**.
- The wrapper sets `HAL0_AGENT_ID` per the bootstrap plan; agents that
  read the env var for their own client-side bookkeeping get the
  right value.

What does *not* work yet:

- The audit log stamps every MCP call as `client_id="anonymous"` (or
  the literal bearer string, if one was passed) regardless of which
  agent made it.
- `memory_add` with `dataset="private:<agent_id>"` lands in `shared`
  per #317. Identity cards are unaffected (they use a different
  dataset).

## See also

- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md)
- [ADR-0012 — Remove auth and Caddy entirely](../../internal/adr/0012-remove-auth-and-caddy.md)
- [Issue #317 — `memory_add` normalizes dataset to "shared"](https://github.com/Hal0ai/hal0/issues/317)
- [`docs/internal/hermes-bootstrap-plan-2026-05-23.md` §8](../internal/hermes-bootstrap-plan-2026-05-23.md) — bootstrap-side header wiring.
- [`memory/private-namespacing.md`](../memory/private-namespacing.md) — the memory-side half of the same gap.
