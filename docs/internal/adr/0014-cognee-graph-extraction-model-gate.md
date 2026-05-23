# ADR 0014 — Cognee graph extraction model gate (v0.3)

- **Status:** Accepted
- **Date:** 2026-05-23
- **Drivers:** PLAN.md §1 v0.3 stream #5 "Cognee graph extraction (Kuzu) gated behind a configurable model"; v0.3 ships-when criterion; ADR-0005 §6 explicitly deferred this decision to a follow-up
- **Supersedes:** ADR-0005 §6 graph-extraction bullet (the "Phase 9" deferral)
- **Related:** ADR-0005 (memory engine = Cognee), ADR-0008 (Lemonade adoption — what `primary` slot looks like), risk PLAN.md §17 "Cognee structured-output reliability on small local models"

## Context

Cognee's graph build pipeline (Kuzu) extracts entities + relations from
ingested text using an LLM with structured-output prompts. ADR-0005 §6
deferred this to "Phase 9" on the explicit grounds that small local
models (qwen3:8b-class) flake on the structured-output format. v0.3
promoted the feature into scope but PLAN.md §1 left the model-selection
mechanism open: *"gated behind a configurable model and at least one
MCP-client external source connectable from a bundled agent"*.

PLAN.md §17 keeps the same risk row open: *"Cognee structured-output
reliability on small local models (Phase 9 graph builds)"* with the
mitigation listed as *"Gate graph extraction behind a configurable
model; default to OpenRouter or 70B-class local model for graph builds.
Basic memory (add/search/list/delete) works on any model."*

What's unsettled and needs a decision:

1. **Which slot/route carries graph extraction by default?** Existing
   options: `primary` slot (whatever the user picked at install), a
   new dedicated `agent` slot, an upstream route (OpenRouter /
   Anthropic / OpenAI / custom OpenAI-compatible), or no default
   (force user choice at enable time).
2. **What's the default ON/OFF state in v0.3?** Forcing graph builds
   on by default risks broken/empty graphs for users on small local
   models.
3. **Do we ship an eval gate?** Without one, "configurable" is just
   "your problem" — the failure mode (low-quality graph) is silent.
4. **Privacy trade-off disclosure.** Routing to upstream means the
   ingested memory text leaves the box. Single-box home-AI identity
   (per ADR-0001 / ADR-0005) cuts against silent upstream calls.

## Options considered

| Option | Reason rejected (or accepted) |
|---|---|
| **Default ON via `primary` slot** | Quality cliff: most users land on 7B/8B local models at install (per PLAN.md §15 Phase 4 wizard). Structured-output failure rate too high to enable silently. Surfaces as "memory works but graph view is empty." |
| **Default ON via upstream route** | Cleanest quality story but every memory write becomes an exfil event. Contradicts the single-box trust posture user picked at install. Loud + non-obvious. |
| **Default OFF; user opts in via dashboard, picks route at enable time** | ACCEPTED. Explicit opt-in matches Cognee's own "graph_enabled = false" default (verified against upstream `cognee/config.py`). Surfaces the trade-off where the user makes it. |
| **Force route choice with no default** | Same as accepted, but blocks "enable + go" flow. Rejected as friction. |
| **Ship eval suite first; auto-flip ON when local model passes** | ACCEPTED as v0.4 path. Eval suite (audit gap G2) needed regardless. Too much surface for v0.3 ship. |

## Decision

### 1. Graph extraction defaults OFF in v0.3

- `[memory.graph] enabled = false` in installed `hal0.toml`.
- `hal0-memory` MCP exposes `memory_search` with vector-only path when
  `enabled = false` — same behavior as v0.2, no graph build runs on
  `memory_add`.
- Cognee's `graph_enabled` config flag tied to ours one-for-one; no
  background indexing when off.

### 2. When enabled, route picked from a typed enum

```toml
[memory.graph]
enabled = true
# One of: "upstream", "primary", "agent"
route = "upstream"

[memory.graph.upstream]
# Required when route = "upstream".
# Format mirrors upstreams.toml entries.
provider = "openrouter"        # or "anthropic" | "openai" | "custom"
model    = "anthropic/claude-3.5-sonnet"
# api_key resolved from upstreams.toml's [providers.openrouter.api_key]
```

- `route = "upstream"` — resolved via the existing upstreams
  (`hal0/upstreams/`) machinery. No new credential path.
- `route = "primary"` — uses the live `primary` slot. Quality on user's
  responsibility.
- `route = "agent"` — uses a dedicated `agent` slot if present
  (typically a larger model on Strix Halo unified RAM). Per ADR-0008 +
  ADR-0009 the `agent` slot is the FLM NPU path on Strix Halo;
  graph-extraction prompts must still be CPU/GPU-validated.

### 3. Default route at enable time = `upstream` (when a provider is configured)

- Dashboard's Memory tab "Enable graph extraction" toggle shows a
  one-screen disclosure: *"Graph extraction sends ingested memory text
  to <chosen upstream> for entity + relation extraction. Your raw
  memory store stays local. Switch to a local slot to keep everything
  on-box (quality may vary on small models)."*
- If no upstream provider is configured at toggle time, the toggle
  goes straight to a route-picker that lists configured local slots.
- CLI parity: `hal0 memory graph enable [--route=upstream|primary|agent]
  [--provider=...] [--model=...]`.

### 4. Eval gate is a v0.4 deliverable, not v0.3

- v0.3 ships the gate + config + dashboard surface; the suite that
  *measures* quality per (route × model) ships in v0.4 (audit gap G2
  per `audit-2026-05-22-phase8-skill-review.md`).
- Until then, the "enable" toggle copy includes a soft caveat:
  *"Graph quality varies by model. We don't currently measure it for
  you — your results may vary."*

### 5. Privacy posture (explicit)

- `route = "upstream"` is the default suggestion but **NEVER** the
  default behavior — graph extraction is off until the user toggles.
- The disclosure copy is part of the contract: changing it requires
  amending this ADR.
- `route = "primary"` and `route = "agent"` keep memory text on-box.

### 6. Storage + retrieval contract unchanged

- `memory_add` API surface unchanged. When `graph.enabled = true`, the
  add call enqueues a background graph build; the foreground call
  returns the same `{id, timestamp}` shape as today.
- `memory_search` gains an optional `mode: "vector" | "graph" | "hybrid"`
  parameter; default stays `"vector"` for backward compat.
- Failed graph builds log + drop silently — never block ingestion.
  Surfaced in dashboard Memory tab as a per-build error count.

## Consequences

### Positive

- Cleanly resolves PLAN.md §1 v0.3 ships-when item without enabling
  a feature that flakes on the median user's hardware.
- Reuses existing upstream-provider machinery — no new credential
  path, no new config namespace.
- Explicit privacy disclosure at toggle time matches ADR-0001's
  "home-LAN open by default, password opt-in" pattern of putting the
  decision in front of the user, not in a config file.
- Future-compatible: when the eval suite ships in v0.4 + a local
  model proves reliable enough, we can flip the default route or
  even auto-enable for that model family — additive, no migration.

### Negative / costs

- Three routes to test + document means three failure modes the
  dashboard has to surface coherently. Mitigation: each route reuses
  an existing health-check path (upstreams already report status;
  slots already report state).
- "Default off" means graph features look broken on first install
  ("why is the graph view empty?"). Mitigation: dashboard Memory tab
  shows a clear "Graph extraction is off" state with a one-click
  enable affordance.
- Tying graph-build to user-configurable upstream means changing the
  upstream changes graph quality silently. Mitigation: the per-build
  error count + a model-fingerprint stamp on each graph node so
  re-routing is traceable.

## Pending items

- Cognee version + the exact `graph_enabled` config flag verified
  against the pin in `pyproject.toml`.
- Dashboard Memory tab — "graph extraction" section (one toggle,
  one route picker, one disclosure, one error counter). Tracked
  separately under dashboard-v3 (closes part of #228).
- `[memory.graph]` TOML schema added to `src/hal0/config/schema.py`.
- CLI surface: `hal0 memory graph {enable,disable,status}`.
- Eval-suite scaffolding deferred to v0.4 (audit gap G2 issue, to be
  filed).
- Docs page `docs/memory/graph.md` written before the toggle ships.
