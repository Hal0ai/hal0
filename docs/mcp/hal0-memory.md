---
title: hal0-memory MCP server
description: Cognee-backed long-term memory exposed over MCP. Four tools — add, search, list, delete. Per-agent namespaces, dataset model, source-stamped audit. Mounted at /mcp/memory.
sidebar:
  order: 3
---

`hal0-memory` is the **long-term memory** MCP server. It exposes four
tools backed by the Cognee engine (per
[ADR-0005](../../internal/adr/0005-memory-engine-cognee.md)) and is
the canonical persistence layer for "what hal0 remembers about the
user / the homelab / agent peers".

- **Mount:** `/mcp/memory`
- **Transport:** Streamable HTTP (FastMCP)
- **Source:** [`src/hal0/mcp/memory.py`](https://github.com/Hal0ai/hal0/blob/main/src/hal0/mcp/memory.py)
- **Wired by:** `src/hal0/api/mcp_mount.py:154` (`build_memory_server`)

The wrapper around Cognee itself lives in
`src/hal0/memory/cognee_wrapper.py`; this module is just the MCP
tool surface + schema validation.

## Tool taxonomy

Four tools. Full schema + per-tool implementations are at
`src/hal0/mcp/memory.py:167` (`_memory_add`), `:220` (`_memory_search`),
`:269` (`_memory_list`), `:292` (`_memory_delete`).

| Tool            | Required args            | Returns                                       |
|-----------------|--------------------------|-----------------------------------------------|
| `memory_add`    | `text`                   | `{id, timestamp}`                             |
| `memory_search` | `query`                  | `{results: [{id, text, score, timestamp, dataset, tags, source, metadata}, ...]}` |
| `memory_list`   | (none)                   | `{items, next_cursor}`                        |
| `memory_delete` | `ids`                    | `{deleted}`                                   |

### Optional args

- `memory_add`: `dataset`, `tags`, `metadata`.
- `memory_search`: `limit` (default 10), `dataset` (default `shared`),
  `tags`, `before`, `after`.
- `memory_list`: `dataset` (default `shared`), `cursor`, `limit`
  (default 50).

### What clients **cannot** pass

`memory_add` rejects any caller-supplied `source` field with
`MemorySchemaError` (`src/hal0/mcp/memory.py:202`). The `source`
column is server-injected from `client_id` per ADR-0005 §5 —
the audit trail stays forensically grounded. Callers can't lie about
their identity.

## Dataset model

Three conventions (per [ADR-0005 §3](../../internal/adr/0005-memory-engine-cognee.md#1-engine--cognee) + [ADR-0011](../../internal/adr/0011-agent-identity-cards.md)):

| Dataset             | Purpose                                                | Who writes |
|---------------------|---------------------------------------------------------|-------------|
| `shared`            | Cross-agent episodic memory.                            | Anyone.     |
| `private:<client_id>` | Per-agent working memory.                             | The agent itself, via `X-hal0-Private: 1`. **Currently degraded — see [issue #317](https://github.com/Hal0ai/hal0/issues/317).** |
| `agents`            | Service registry — agent identity cards (ADR-0011).     | Each agent at bootstrap. Immutable until re-bootstrap or uninstall. |

Namespace resolution lives at `src/hal0/mcp/memory.py:134`
(`_resolve_dataset`).

See [`memory/private-namespacing.md`](../memory/private-namespacing.md)
for the current state of the `private:*` namespace.

## Transport headers the memory server reads

- `X-hal0-Private: 1` (or `true`) — promote writes to
  `private:<client_id>` per ADR-0005 §3. Resolved in
  `src/hal0/api/mcp_mount.py:73` (`private_resolver`).
- `Authorization: Bearer <token>` — identity-stash only (no auth,
  post-ADR-0012). The server retains the bearer string as `client_id`
  if present, otherwise falls back to `"anonymous"`. See
  [`agents/identity.md`](../agents/identity.md) for the gap between
  this and the intended `X-hal0-Agent` header.

## REST shims

PR #303 added REST shims so non-MCP clients (the dashboard, scripts,
direct curl) can call the same surface without an MCP session.
Source: `src/hal0/api/routes/memory.py`.

| MCP tool        | REST equivalent                  | Source           |
|-----------------|----------------------------------|------------------|
| `memory_add`    | `POST /api/memory/add`           | `routes/memory.py:189` |
| `memory_search` | `POST /api/memory/search`        | `:214`           |
| `memory_list`   | `GET  /api/memory/list`          | `:240`           |
| `memory_delete` | `POST /api/memory/delete`        | `:252`           |

Same schema, same dataset model, same headers respected (`X-hal0-Private`
flows the same way). The MCP server uses an in-process Cognee dispatcher;
the REST shims call the wrapper directly.

## Graph extraction

Cognee can also build a **knowledge graph** of entities + relations on
top of every ingested item, powering
`memory_search(mode="graph"|"hybrid")`. **Off by default in v0.3** per
[ADR-0014](../../internal/adr/0014-cognee-graph-extraction-model-gate.md).
See [`memory/graph.md`](../memory/graph.md) for the toggle + the route
trade-offs.

## Where the data lives

Cognee's defaults — all embedded, file-based, zero external services:

- `/var/lib/hal0/memory/cognee/sqlite/` — relational + documents.
- `/var/lib/hal0/memory/cognee/lancedb/` — vector store.
- `/var/lib/hal0/memory/cognee/kuzu/` — graph store (used only when
  graph extraction is enabled).

Swap-out support exists for Postgres / Qdrant / Neo4j upstream;
hal0 stays on defaults per ADR-0005 §1.

## Audit

Every tool call emits a structured row through the `hal0.mcp.audit`
logger, same shape as hal0-admin. See [`hal0-admin.md#audit`](./hal0-admin.md#audit).

## Fail-fast on missing Cognee

When the `cognee` package is not installed (it ships with the hal0
wheel's memory-engine extras), `import hal0.mcp.memory` raises
ImportError early — see `src/hal0/mcp/memory.py:72`. The mount in
`mcp_mount.py:151` skips the memory server gracefully if the wrapper
is `None`, so tests that don't exercise memory still boot cleanly;
production always wires it.

## See also

- [`overview.md`](./overview.md) — transport, mount, identity, host model.
- [`hal0-admin.md`](./hal0-admin.md) — sibling bundled server (drives the host).
- [`memory/overview.md`](../memory/overview.md) — engine, REST surface, on-disk layout.
- [`memory/graph.md`](../memory/graph.md) — graph extraction toggle.
- [ADR-0005 — Memory engine = Cognee](../../internal/adr/0005-memory-engine-cognee.md)
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md)
