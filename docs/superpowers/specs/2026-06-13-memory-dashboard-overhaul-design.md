# Memory dashboard overhaul (v2) — design

Date: 2026-06-13 · Branch: `feat/memory-overhaul` · Source design:
`/home/halo/hal0-design/memory_overhaul` (README + IMPLEMENTATION.md + prototype).

## Context / load-bearing fact

The v1 Hindsight Memory dashboard already shipped (PRs #747–749, on `origin/main`):

- **Backend** `src/hal0/api/routes/memory_admin.py` — allowlisted forward proxy
  exposing the entire Hindsight 0.7.x surface (`/api/memory/banks/*`: graph,
  entities/graph, stats, timeseries, recall, reflect, documents, mental-models,
  directives, operations, consolidate, import/export) + a fail-soft
  `/api/memory/engine` aggregator.
- **Hooks** `ui/src/api/hooks/useHindsight.ts` — one hook per resource, all
  wired to the proxy. `d3-force` is a dependency; force primitives + every hook
  are republished on `window.__hal0*` by `ui/src/dash/memory-hook-bridge.ts`.
- **Route** `#memory` (Overview · Graph · Tools) in `memory.jsx` /
  `memory-graph.jsx` / `memory-tools.jsx`.

So this overhaul is **frontend-only**. No new endpoints, no new deps. The graph
is the real target: today it is the v1 "hairball" (synchronous force, viewBox
zoom only, no drag, `<title>` tooltips). The `#agent` route still carries dead
Peers/Namespaces/Cognee-fixture code the design folds away.

## Goals

1. Replace the graph with a reusable **engine** (drag/pin, pan/zoom, hovercard,
   detail, path-trace, helpers) + **three view directions**:
   - **A** Lensed force graph (edge-type lenses, fact-type filter, search,
     ego-focus, path-trace, auto-fit).
   - **B** Structured lenses (semantic→clusters, temporal→timeline+scrub cursor,
     causal→L→R DAG, cooccurrence→adjacency matrix) with FLIP tweens.
   - **C** Ego explorer (centre + neighbour ring, click-to-walk, breadcrumb,
     timeline strip) — renders only a local neighbourhood, scales to any bank.
   An inline A·B·C switch top-right; scale banner suggests B/C above ~240 nodes.
2. **Re-skin Overview & Tools** to the prototype look (`mo-*` / `mt-*`) while
   keeping **all live hooks** — no CRUD regression.
3. **Fold Agent → Memory**: delete Peers + Namespaces + Cognee fixtures; collapse
   `#agent` to a thin pointer card linking `#memory`.
4. Smooth animations (drag reheat, layout-switch FLIP, ego-walk, timeline scrub,
   cursor-anchored zoom, hovercard fade) gated on `prefers-reduced-motion`;
   keyboard a11y (focus, arrow-walk, Enter/`f`/`p`). Persist `{direction, layout,
   scrubT}` per bank to localStorage.

## Architecture (window-globals `.jsx` convention, matching the host)

| File | Action | Owner |
|---|---|---|
| `dash/memory-graph-engine.jsx` | **new** kernel + `normalizeGraph` + color maps + `useTween`/`useSize` | foundation (me) |
| `dash/memory-graph.jsx` | **rewrite** — `GraphLensed` (A) + `MemGraphExplorer` wrapper (bank/source/search/**A·B·C switch**/scale banner) | agent A |
| `dash/memory-graph-structured.jsx` | **new** — `GraphStructured` (B) + `CooccurMatrix` | agent B |
| `dash/memory-graph-ego.jsx` | **new** — `GraphEgo` (C) | agent C |
| `dash/memory.jsx` | **re-skin** Overview `mo-*`, live hooks | agent Overview |
| `dash/memory-tools.jsx` | **re-skin** Tools `mt-*`, live hooks | agent Tools |
| `dash/agents/memory-tab.jsx`, `agent-view.jsx` | **gut→pointer** (`ag-*`) | agent Fold |
| `dash/memory-overhaul.css` | **new** — tokens + `mg-*/mo-*/mt-*/ag-*` (imported after dashboard.css) | foundation |
| `dash/memory-hook-bridge.ts` | add `forceX,forceY` | foundation |
| `dash/chrome.jsx` | add `GLYPHS` + `name`-prop `Icon` | foundation |
| `main.tsx` | css + engine/structured/ego import order | foundation |
| `api/mock.ts` | mock builders + allowlist for graph/entities/banks | agent Test |

## Data contract — the one adaptation

Live payloads are Cytoscape `{nodes:[{data}], edges:[{data}], total_*}`. All three
directions consume **one normalized graph** from `useBankGraph` (facts) /
`useEntityGraph` (entities) via `normalizeGraph(payload, source)`:

- **fact node** → `{id, kind:'fact', label, text, type:'world'|'experience'|
  'observation', date, t, ents[], topic, topicLabel, topicColor, color}`
- **entity node** → `{id, kind:'entity', entKind, label, mentionCount, color}`
- **link** → `{id, source, target, linkType:'semantic'|'temporal'|'causal'|
  'cooccurrence', weight}` (self-loops dropped)
- returns `{nodes, links, topics:{[id]:{label,color}}}`

Hindsight emits no `topic` field; `normalizeGraph` **derives topics from
connected components over semantic edges** (fallback: fact-type). `causal` is
kept as a first-class lens; it simply renders empty if the engine emits none
(consistent with v1's legend).

### Direction prop contract (wrapper → directions)
- `GraphLensed({ graph, source, query, width, height, banner })` — `graph` is the
  active normalized graph (entity graph when `source==='entities'`).
- `GraphStructured({ graph, entityGraph, query, width, height, banner })` — fact
  graph for clusters/timeline/DAG; entity graph for the matrix.
- `GraphEgo({ graph, query, width, height, banner })` — fact graph ego.

## State (lifted to `MemGraphExplorer`, shared across A/B/C)
`selected/hover/pinned/lenses/factTypeFilter/search/egoRoot/egoDepth/pathFrom/
pathTo/layout/scrubT/transform`; persist `{direction, layout, scrubT}` per bank.

## Verification
- Mock builders + allowlist for `/api/memory/banks`, `/banks/{}/graph`,
  `/banks/{}/entities/graph` (γ-suite forced-mock).
- Playwright mock-mode screenshots of A/B/C + Overview + Tools; interaction smoke
  (drag, lens toggle, dir switch, ego walk).
- `vite build` + `tsc` clean. Then **deploy CT105** (`scripts/deploy.sh`) and
  live-validate against real banks before done.

## Risks / decisions
- No `topic` from Hindsight → derived (above).
- Re-skin must not regress live CRUD → visuals change, hooks/actions stay.
- Large banks → client-side ego (C) on a `limit`-capped fetch; no server ego
  endpoint (Hindsight has none) — server-side subgraph "expand" deferred as a
  follow-up, logged via `log()`/issue.
- Old graph replaced outright (git-recoverable); no fallback flag.
