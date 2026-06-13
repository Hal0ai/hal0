# Memory subgraph endpoint — server-side ego / top-K for large banks

**Date:** 2026-06-13
**Status:** Design approved, pre-implementation
**Branch:** `feat/memory-subgraph-endpoint`
**Follows:** `2026-06-13-memory-dashboard-overhaul-design.md` (Follow-up 2 of that overhaul)
**Independent of:** Follow-up 1 (client-side ego ring cap, branch `feat/memory-ego-cap`)

## Problem

The memory graph explorer (`ui/src/dash/memory-graph.jsx`) always fetches the
**whole** bank graph — fact graph via `useBankGraph(bank,{limit:300})` and entity
graph via `useEntityGraph(bank,{min_count:1,limit:500})` — and does all
filtering / ego-slicing **client-side** in `normalizeGraph`. This is fine at the
hundreds of nodes seen today (largest live bank `shared` = 154 facts, ~4900
edges) but does not scale:

- The payload, the d3 force sim (Directions A/B), and the DOM all grow with
  **total** node count.
- Direction C (Ego) only *renders* a local neighbourhood but still *fetches and
  normalises the entire graph* to compute it.
- The `limit:300/500` already bounds the fetch, but it is a **blind
  head-truncation** by Hindsight — at thousands of units it returns an
  arbitrary, possibly-disconnected slice with no relevance or connectivity
  guarantee. Over 240 nodes the UI shows only a *static* advisory banner
  (`memory-graph.jsx:451`); there is no meaningful bounded fetch and no expand.

A real large bank is coming online, fed by **document ingestion** (chunk- and
document-dense, many semantic edges — degree matters) **and chat/agent history**
(fact-heavy, recency matters). Both the fetch cost and the meaningless-slice
problem will bite.

### The upstream blocker

**Hindsight 0.7.x exposes no ego / top-K / subgraph query.** `GET
/v1/default/banks/{bank}/graph` accepts only `type, limit, q, tags, tags_match,
document_id, chunk_id` — no "neighbours of node X to depth D", no "top-K by
degree". So a true bounded slice cannot be expressed as a pure passthrough; it
must be **composed server-side** in hal0-api. (Per the standing "third-party →
official fix first" rule, an upstream Hindsight feature request for a native
subgraph query is the best long-term fix and should be filed/tracked — but it is
slow and does not block this near-term win. See Out of scope.)

## Approach (selected)

**Option 2 — hal0-api composition layer.** Add one composed endpoint in
`src/hal0/api/routes/memory_admin.py` that mirrors the existing `/engine`
fail-soft aggregator (`memory_admin.py:141`): pull `hindsight_client` off
`app.state`, fetch the bank graph from Hindsight once (cached), compute the
slice in Python, and return the **existing `GraphPayload` Cytoscape shape** so
the client adapter `normalizeGraph` is unchanged.

This endpoint is **composed**, not a passthrough — it must **not** be added to
the dumb `_FORWARDS` table.

### Rejected alternatives

- **Option 1 (frontend-only interim):** approximate ego by querying around the
  selected node's text/entities with `q=`. Not a true BFS ego, no degree
  ranking, no real bound on disconnected slices. Lower effort but does not solve
  the core problem; rejected as the primary path.
- **Option 3 (upstream Hindsight feature):** best long-term, slowest; tracked
  separately, does not block (see Out of scope).

## Design decisions

### A — Caching: per-bank in-memory TTL cache (selected)

Cache the **raw Hindsight graph** server-side, keyed by `(bank, kind, type, q)`,
with a short TTL (default 45s; configurable). Ego/top-K are computed from the
cached graph, so repeated pans / expands / centre-changes do not re-pull the
whole upstream graph.

- Bounded staleness; no coupling to mutation routes. The client already carries
  `staleTime: 15s`, and the dashboard is read-mostly.
- Rejected A1 (no cache — re-fetches full upstream graph every request) and A3
  (cache + invalidate on add/delete/consolidate — couples the composition layer
  to mutation routes; YAGNI now, TTL is sufficient).
- Cache is a tiny module-level `TTLCache`-style dict guarded for size (cap a
  handful of banks; evict oldest). Implemented in a small helper so it is unit-
  testable in isolation (inject a clock).

### B — Endpoint surface: one endpoint, mode params (selected)

```
GET /api/memory/banks/{bank_id}/graph/subgraph
    ?kind=memories|entities          (default memories)
    &mode=ego|top                    (default top)
    # ego mode:
    &node=<node_id>                  (required for ego)
    &depth=1|2                       (default 1, max 2)
    # top mode:
    &top_k=<int>                     (default 200, max 500)
    &by=degree|recency               (default: degree for entities, recency for memories)
    # both modes:
    &limit=<int>                     (hard payload cap, default 240, max 500)
    &type=<edge_type>&q=<text>       (forwarded to upstream fetch, narrows the source graph)
```

One handler, one hook (`useBankSubgraph`), one mock. Rejected B2 (two endpoints
`/graph/ego` + `/graph/top`) — 2× surface for no real gain.

### C — UX / "expand" (selected)

- When `big` (> 240 normalised nodes), Directions A/B **auto-load top-K** via
  the new endpoint instead of the blind `limit:300/500` fetch — `by=degree` for
  entities, `by=recency` for memories.
- The over-threshold banner becomes **actionable**: `showing top N of M ·
  [expand]`. Expand steps the cap up (e.g. +200) or loads the full graph; state
  always shows N-of-M. **No silent caps** (standing rule) — any truncation is
  surfaced in the banner.
- Direction C (Ego) calls `mode=ego&node=<center>&depth=1` on centre-change,
  fetching only the local neighbourhood. (The *render-side* ring cap for
  high-degree nodes is Follow-up 1; this endpoint bounds the *fetch*.)
- Small banks (≤ threshold) keep today's full-graph behaviour unchanged.

## Data flow & algorithm

1. **Fetch (cached):** GET the bank graph from Hindsight once per `(bank, kind,
   type, q)` within the TTL window. `kind=memories` → `/v1/default/banks/{b}/graph`;
   `kind=entities` → `/v1/default/banks/{b}/entities/graph`. Payload is Cytoscape
   `{nodes:[{data:{id,…}}], edges:[{data:{source,target,type,weight,…}}]}`.
2. **Build adjacency** from the edge list (tolerant of `source|from` /
   `target|to`, `type|linkType`).
3. **top mode:**
   - `by=degree`: rank nodes by **salience-weighted degree** — sum over incident
     edges of `typeWeight(linkType) * (edge.weight ?? 1)`, where
     `causal > temporal > cooccurrence > semantic`. Take top_k.
   - `by=recency`: rank nodes by a **tolerant timestamp** (`t` / `created_at` /
     `timestamp` / `updated_at`, first present), newest first. Take top_k.
   - **Induce** the subgraph: keep edges whose *both* endpoints are in the chosen
     node set. Then apply the hard `limit` (drop lowest-ranked nodes + their
     edges if still over).
4. **ego mode:** BFS from `node` to `depth` (max 2) over the undirected
   adjacency; collect the reached node set, capped at `limit` (ring-1 prioritised
   over ring-2; within a ring, salience order as above). Induce edges among the
   reached set.
5. **Return** the same Cytoscape shape plus scope counters:
   ```json
   {
     "nodes": [{"data": {...}}],
     "edges": [{"data": {...}}],
     "total_units":    <int>,   // full bank fact/unit count (from source)
     "total_entities": <int>,   // full bank entity count (from source)
     "total_edges":    <int>,   // full bank edge count (from source)
     "returned_nodes": <int>,   // nodes in THIS slice
     "returned_edges": <int>,
     "truncated":      <bool>,  // returned_nodes < in-scope node count
     "mode": "ego|top", "by": "...", "center": "<node|null>"
   }
   ```
   Only `nodes/edges/total_*` are consumed by `normalizeGraph` (unchanged); the
   extra fields drive the "N of M · expand" banner. The node/edge `data` objects
   are passed through **verbatim** from Hindsight so every downstream field
   (`entities`, timestamps, `type`, `weight`, entity `mention_count`) survives.

## Components & files

**Backend**
- `src/hal0/api/routes/memory_admin.py` — new `@router.get(".../graph/subgraph")`
  composed handler (mirrors `/engine`; uses `_client`, `_forward`-style error
  mapping). Not in `_FORWARDS`.
- `src/hal0/api/routes/_memory_subgraph.py` *(new, small)* — pure functions:
  `rank_by_degree`, `rank_by_recency`, `ego_bfs`, `induce_subgraph`,
  `type_weight`, plus the TTL graph cache (`GraphCache` with injectable clock).
  Kept separate so the graph math is unit-tested without FastAPI/httpx.

**Frontend**
- `ui/src/api/endpoints.ts` — `memoryBankSubgraph(bank)`.
- `ui/src/api/hooks/useHindsight.ts` — `useBankSubgraph(bank, opts)` returning
  `GraphPayload` (+ optional scope fields); same query/`staleTime` conventions.
- `ui/src/dash/memory-hook-bridge.ts` — export `window.__hal0UseBankSubgraph`.
- `ui/src/dash/memory-graph.jsx` — wrapper: when `big`, route A/B through
  `useBankSubgraph` (top mode); actionable "top N of M · expand" banner;
  Direction C centre-change → ego mode.
- `ui/src/api/mock.ts` — synthesise a large bank (e.g. ~600 fact nodes, mixed
  degree/recency) in `buildMemFactGraph`; serve `/graph/subgraph` (ego + top)
  from the baked dataset (forced-mock short-circuits `page.route`, so e2e asserts
  the baked data).

`normalizeGraph` (`ui/src/dash/memory-graph-engine.jsx`) is the single client
adapter and stays **unchanged**.

## Error handling

- Mirror `/engine` fail-soft posture for the *composition* itself, but this
  endpoint **may** return 4xx for bad input (missing `node` in ego mode →
  `422`; unknown `node` → `404`; bad `kind/mode/by` → `422`). Upstream
  unreachable → reuse `MemoryEngineUnreachable` mapping from `_forward`.
- Empty bank → `{nodes:[],edges:[],total_*:0,...}` (200), not an error.

## Testing (TDD)

**Backend unit tests** (`tests/api/test_memory_subgraph.py`, mirror `/engine`
aggregator test style — fake client returning canned Cytoscape graphs):
- `rank_by_degree` weights causal > temporal > cooccurrence > semantic and
  respects edge `weight`.
- `rank_by_recency` orders by tolerant timestamp; missing-timestamp nodes sort
  last deterministically.
- `ego_bfs` depth-1 vs depth-2 reach sets; `limit` cap prioritises ring-1; never
  includes the center twice; isolated node → just the center.
- `induce_subgraph` keeps only edges with both endpoints in the set; passes node
  `data` through verbatim.
- `GraphCache` returns cached graph within TTL, re-fetches after expiry
  (injected clock); size-bounded eviction.
- Route: ego missing `node` → 422; unknown node → 404; empty bank → 200 empty;
  upstream unreachable → mapped error; `total_*` reflect full-bank counts while
  `returned_*` reflect the slice.

**Frontend / e2e** (`tests/e2e/specs/memory-graph-explorer-v3.spec.ts` or new):
- Against the baked large mock bank, A/B show "top N of M" and **expand** raises
  the count; Direction C ego fetch returns only the local neighbourhood.
- Small-bank behaviour unchanged (no banner, full graph).

## Verification (no CT105 mutation)

`VITE_API_TARGET=http://10.0.1.142:8080 npx vite --port 52xx` → browse
`#memory/graph` against the real `shared` bank; confirm A/B top-K and C ego
fetch hit `/api/memory/banks/shared/graph/subgraph` and render a bounded,
*connected* slice. Then deploy via `bash scripts/deploy.sh` after `wip hal0
claim` (it hard-resets the shared checkout).

## Out of scope (tracked separately)

- **Upstream Hindsight native subgraph query** (option 3): file/track an upstream
  issue per the standing third-party rule; revisit the composition layer to
  delegate once available.
- Cache invalidation on mutation (A3): revisit only if 45s staleness proves
  visible during heavy ingestion.
- Follow-up 1 (client ego ring cap) — separate branch/PR.

## Conventions to respect

- **window-globals .jsx pattern:** dash `.jsx` reference siblings as bare globals
  and register via `Object.assign(window,{…})`; never ES-import across dash
  modules. Load order in `ui/src/main.tsx`.
- **Forced-mock:** `VITE_MOCK_HAL0=1` short-circuits `window.fetch` before
  Playwright `page.route` — e2e asserts the baked dataset in `mock.ts`, not
  per-test stubs. Add fixtures to `mock.ts`.
- **Hindsight data reality:** live fact nodes have no `type`/`topic` and
  `entities` as a comma-string; the endpoint passes `data` through verbatim and
  must not assume the prototype's rich fields. Tolerant timestamp/field
  extraction; verify field names against the live `shared` bank during
  implementation.
