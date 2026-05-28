---
title: Memory in hal0
description: Cognee-backed long-term memory. SQLite + LanceDB + Kuzu, all embedded. Dataset model (shared/private/agents), REST shims, MCP tools, optional graph layer.
sidebar:
  order: 1
---

hal0's long-term memory is a single Cognee-backed surface every agent
on the host shares. Bundled agents, external MCP clients, the
dashboard, and scripts all hit the same data store through one of two
equivalent surfaces: the `/mcp/memory` MCP server or the
`/api/memory/*` REST shims.

The engine choice is fixed:
[ADR-0005](../../internal/adr/0005-memory-engine-cognee.md) picked
Cognee for the v0.2+ memory layer. Bundle, don't build — same stack
we'd write ourselves, Apache-2.0, all-embedded defaults, opinionated
but composable.

## What "memory" is, what it isn't

| Is                                                              | Isn't                                          |
|------------------------------------------------------------------|------------------------------------------------|
| Cross-app persistence the homelab owner remembers across agents. | Per-agent prompt-scratchpad.                   |
| Vector + optional graph search over arbitrary text.              | A RAG document store (no per-doc retrieval semantics). |
| Source-stamped (`client_id` = who wrote it).                    | Anonymous.                                     |
| Per-agent isolatable via `private:<client_id>`.                 | Multi-tenant. Single-host owner only.          |

The term overloads inside hal0 — `pi-memory-md` is pi-coder's
project-scoped markdown memory and is **not** the same thing as the
hal0 memory MCP. See `CONTEXT.md`.

## Engine

[Cognee](https://github.com/topoteretes/cognee), Apache-2.0. Defaults
hal0 ships with (per ADR-0005 §1):

- **SQLite** — relational + documents.
- **LanceDB** — vector store.
- **Kuzu** — graph store. Only populated when graph extraction is on.
- **fastembed** — CPU embedder for ingestion.

All four are embedded, file-based, zero external services. Cognee
supports swapping in Postgres / Qdrant / Neo4j; hal0 stays on
defaults.

## Where data lives

Single root: `/var/lib/hal0/memory/cognee/`. Under it:

| Subdir       | Contents                          |
|--------------|------------------------------------|
| `sqlite/`    | Relational records + documents.    |
| `lancedb/`   | Vector indexes per dataset.        |
| `kuzu/`      | Graph store (graph mode only).     |
| `logs/`      | Cognee internal logs.              |

The directory is owned by the hal0 service user. Operators can
back up `/var/lib/hal0/memory/cognee/` as a unit.

## Dataset model

Three conventions (per ADR-0005 §3 and
[ADR-0011](../../internal/adr/0011-agent-identity-cards.md)):

- **`shared`** — cross-agent episodic memory. The default for every
  `memory_add` that doesn't override.
- **`private:<client_id>`** — per-agent working memory. Opted into
  via the `X-hal0-Private: 1` transport header. **Currently degraded
  to `shared` — see [issue #317](https://github.com/Hal0ai/hal0/issues/317)
  and [`private-namespacing.md`](./private-namespacing.md).**
- **`agents`** — service registry. Holds the immutable identity cards
  per agent (ADR-0011). Searchable by similarity so peer-agent
  discovery doubles as "find an agent that can do X".

Namespace resolution lives in `src/hal0/mcp/memory.py:134`
(`_resolve_dataset`). Callers can pass `dataset=` explicitly; the
`X-hal0-Private: 1` toggle wins over an explicit value when both are
set.

## How to read + write

Two interchangeable surfaces. Same schema, same datasets, same
headers respected.

### MCP

| Tool            | Required args | Returns                          |
|-----------------|---------------|----------------------------------|
| `memory_add`    | `text`        | `{id, timestamp}`                |
| `memory_search` | `query`       | `{results: [...]}`               |
| `memory_list`   | (none)        | `{items, next_cursor}`           |
| `memory_delete` | `ids`         | `{deleted}`                      |

See [`mcp/hal0-memory.md`](../mcp/hal0-memory.md) for the full schema.

### REST shims (PR #303)

| Method + path                  | Purpose         |
|--------------------------------|-----------------|
| `POST /api/memory/add`         | Same as `memory_add` tool. |
| `POST /api/memory/search`      | Same as `memory_search` tool. |
| `GET  /api/memory/list`        | Same as `memory_list` tool. |
| `POST /api/memory/delete`      | Same as `memory_delete` tool. |

Source: `src/hal0/api/routes/memory.py:189` onward. Useful when an
operator wants to script against memory without standing up an MCP
session.

### CLI

```sh
# Memory graph toggle (the only memory-specific CLI today).
hal0 memory graph status
hal0 memory graph enable --route primary
hal0 memory graph disable
```

Source: `src/hal0/cli/memory_commands.py`. Direct
add/search/list/delete via CLI is **not** wired in v0.3 — use the
REST shims or the MCP tool.

## Source stamping

Every `memory_add` is server-injected with `source = client_id`. Callers
**cannot** pass `source` themselves — the tool rejects with
`MemorySchemaError` (`src/hal0/mcp/memory.py:202`). This is the
audit-grounding rule from ADR-0005 §5.

Today `client_id` comes from the `Authorization: Bearer` header (or
falls back to `"anonymous"`). Per ADR-0012, the intended source
post-rename is the `X-hal0-Agent` header — see
[`agents/identity.md`](../agents/identity.md).

## The graph layer (ADR-0014)

Cognee can build a knowledge graph of entities + relations on top of
every ingested item, powering `memory_search(mode="graph")` and
`mode="hybrid"`. The graph build prompts an LLM for
structured output; small local models flake on the prompts often
enough that **graph extraction is off by default in v0.3**.

Enable + route picker:

```sh
hal0 memory graph enable --route primary
hal0 memory graph enable --route upstream --provider openrouter --model anthropic/claude-3.5-sonnet
```

Full details + the privacy / quality trade-off table:
[`graph.md`](./graph.md).

## See also

- [`graph.md`](./graph.md) — graph extraction toggle, the three routes, failure modes.
- [`private-namespacing.md`](./private-namespacing.md) — per-agent `private:*` dataset shape + current gap.
- [`mcp/hal0-memory.md`](../mcp/hal0-memory.md) — MCP tool surface.
- [ADR-0005 — Memory engine = Cognee](../../internal/adr/0005-memory-engine-cognee.md)
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md) — what lives in the `agents` dataset.
- [ADR-0014 — Cognee graph extraction model gate](../../internal/adr/0014-cognee-graph-extraction-model-gate.md)
