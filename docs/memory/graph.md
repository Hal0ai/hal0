---
title: Memory graph extraction
description: Cognee graph builds (entities + relations) gated behind a configurable model. Off by default in v0.3 — small local models flake on the structured-output prompts.
sidebar:
  order: 2
---

hal0's [memory engine](./overview.md) (Cognee) can build a **knowledge
graph** of entities + relations on top of every ingested memory item.
The graph powers `memory_search(mode="graph")` and `mode="hybrid")`;
without it, search is vector-only.

Graph extraction is **off by default** in v0.3. See
[ADR-0014](../../internal/adr/0014-cognee-graph-extraction-model-gate.md)
for the full design rationale.

## Why off by default

The graph build runs `cognee.cognify`, which prompts an LLM for
structured (JSON-schema) output. Small local models (qwen3:8b-class,
gemma3:1b, …) flake on those prompts often enough that enabling
graph extraction silently produces empty or wildly incorrect graphs
— and the failure is invisible: vector search keeps working, but
graph-view operations return nothing useful.

ADR-0014 picks **explicit opt-in** over either a "silently broken"
default or a "force a hosted-LLM key on every install" default. You
turn it on when you've decided how much accuracy you need vs how much
data you're willing to send out of the box.

## The three routes

When you enable graph extraction, you pick one **route** — where the
structured-output prompts run.

| Route       | Where the LLM runs                       | Privacy                                  | Quality                                  |
|-------------|------------------------------------------|------------------------------------------|------------------------------------------|
| `upstream`  | OpenRouter / Anthropic / OpenAI / custom | Ingested text leaves the box per build  | Highest (frontier models)                |
| `primary`   | Your `primary` slot                      | Stays on-box                             | Depends on the model — qwen3-27b OK; 7B classes wobble |
| `agent`     | Your `agent` slot (Strix Halo NPU)       | Stays on-box                             | Depends on the model; NPU latency is great but FLM is constrained on context |

The default suggestion at enable-time is **`upstream`** when you
have a provider configured. It's NEVER the default *behavior* —
graph extraction stays off until you toggle.

## Privacy posture

`route = "upstream"` sends the **raw memory text** of each newly-added
item to the configured provider for entity + relation extraction.
Your raw memory store stays local (LanceDB + Kuzu + SQLite live under
`/var/lib/hal0/memory/cognee/`); only the cognify pass reaches out.

If you want to keep everything on-box, switch to `primary` or `agent`.
Quality may vary on small models — see "Quality varies" below.

## Quality varies — we don't measure it yet

v0.3 ships the gate, the config, and the dashboard surface. It does
**not** ship an evaluation suite that measures graph quality per
(route × model). That's a v0.4 deliverable (ADR-0014 §4).

Until then, the enable copy includes a soft caveat:

> Graph quality varies by model. We don't currently measure it for
> you — your results may vary.

Failed graph builds **log silently** and don't block ingestion (per
ADR-0014 §6). The dashboard Memory tab surfaces a per-build error
counter so you can spot a flaky route.

## Config shape

```toml
# /etc/hal0/hal0.toml
[memory.graph]
enabled = true
# One of: "upstream", "primary", "agent"
route = "upstream"

[memory.graph.upstream]
# Required when route = "upstream". Same shape as upstreams.toml.
provider = "openrouter"
model    = "anthropic/claude-3.5-sonnet"
```

The CLI + REST validator enforce the shape — `enabled = true + route
= "upstream"` without `[memory.graph.upstream]` fails with
`config.memory_graph_invalid`.

## How to enable

### CLI (PR #290)

```sh
# Inspect current state
hal0 memory graph status

# Turn on via the primary slot (stays on-box)
hal0 memory graph enable --route primary

# Turn on via OpenRouter (text leaves the box per build)
hal0 memory graph enable \
    --route upstream \
    --provider openrouter \
    --model anthropic/claude-3.5-sonnet

# Turn off — cancels any in-flight builds (ADR-0014 §6)
hal0 memory graph disable
```

Source: `src/hal0/cli/memory_commands.py:46` onward.

`--route=upstream` requires `--provider` + `--model`. The CLI
rejects before sending; the server-side validator
(`config.memory_graph_invalid`) also enforces it.

### REST

| Method + path                       | Purpose            | Source                       |
|-------------------------------------|--------------------|------------------------------|
| `GET  /api/memory/graph/status`     | Live state.        | `src/hal0/api/routes/memory.py:72` |
| `PUT  /api/memory/graph`            | Save new state.    | `:111`                       |

Save flips the live Cognee wrapper — no restart needed.

### Dashboard

Navigate to **Memory → Graph extraction**. The panel shows:

- A clear "Graph extraction is off" state with one-click enable.
- A route radio (`upstream` / `primary` / `agent`).
- For `upstream`: a provider + model select populated from your
  configured upstreams.
- The privacy + quality disclosure copy.

## How `memory_search` mode changes behavior

`memory_search` (over `/mcp/memory` or `/api/memory`) accepts an
optional `mode` parameter:

```jsonc
{
  "query": "what does hal know about strix halo",
  "mode": "graph"      // "vector" (default) | "graph" | "hybrid"
}
```

- `"vector"` is the default; backward-compatible with v0.2.
- `"graph"` searches the entity/relation graph. Falls back to vector
  silently when graph extraction is off (logs an audit event so the
  dashboard can show "you asked for graph but graph is off").
- `"hybrid"` blends vector + graph results.

Graph and hybrid modes only produce graph hits once at least one
ingested item has been cognify'd (i.e., `enabled` was `true` when
the item was added).

## Failure modes

| Symptom                                           | Cause                                                   | Where to look                                                            |
|---------------------------------------------------|---------------------------------------------------------|--------------------------------------------------------------------------|
| Toggle on but graph view stays empty              | First build pending — cognify runs async after add      | `hal0 memory graph status` → `in_flight` counter                          |
| Per-build error counter climbs                    | Structured-output parse failures (model can't comply)   | `journalctl -u hal0-api --grep hal0.memory.graph.build_failed`            |
| `config.memory_graph_invalid` on save             | `enabled=true + route=upstream` without provider+model  | Add upstream block or switch route to `primary` / `agent`                 |
| `memory.unavailable` 503                          | Cognee failed to import / init at boot                  | `journalctl -u hal0-api --grep hal0.memory.init_failed`                   |

## See also

- [ADR-0014 — Cognee graph extraction model gate](../../internal/adr/0014-cognee-graph-extraction-model-gate.md)
- [ADR-0005 — Memory engine = Cognee](../../internal/adr/0005-memory-engine-cognee.md)
- [`overview.md`](./overview.md) — engine, dataset model, on-disk layout.
- [`mcp/hal0-memory.md`](../mcp/hal0-memory.md) — MCP tool surface.
