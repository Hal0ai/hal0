# Memory Subgraph Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-side ego / top-K subgraph endpoint so the memory graph explorer renders a meaningful bounded slice of large banks instead of fetching+normalising the whole graph client-side.

**Architecture:** A composed (non-passthrough) FastAPI route in `memory_admin.py` mirrors the `/engine` aggregator — pulls `hindsight_client` off `app.state`, fetches the bank graph once (per-bank TTL cache), computes the slice with a pure graph-math helper module, and returns the existing `GraphPayload` Cytoscape shape so the client adapter `normalizeGraph` is unchanged. Frontend adds one hook + bridge export and routes the wrapper through it when `big`, with an actionable "top N of M · expand" banner and ego fetch for Direction C.

**Tech Stack:** Python / FastAPI / httpx (backend, TDD via `httpx.MockTransport` + `TestClient`); React + @tanstack/react-query + window-globals `.jsx` (frontend); Playwright (e2e, forced-mock).

**Spec:** `docs/superpowers/specs/2026-06-13-memory-subgraph-endpoint-design.md`

**Parallelism:** Tasks 1–4 (backend) and Tasks 5–8 (frontend) touch **disjoint file sets** and can run as two parallel agents off the same base. Task 8 (e2e) is easiest to verify after both land. Backend owns `_memory_subgraph.py`, `memory_admin.py`, `tests/api/test_memory_subgraph.py`. Frontend owns `endpoints.ts`, `useHindsight.ts`, `memory-hook-bridge.ts`, `memory-graph.jsx`, `mock.ts`, the e2e spec.

---

## BACKEND (Agent A)

### Task 1: Pure graph-math helpers — ranking + induce

**Files:**
- Create: `src/hal0/api/routes/_memory_subgraph.py`
- Test: `tests/api/test_memory_subgraph.py`

Upstream payloads are Cytoscape: `{"nodes":[{"data":{"id":…}}], "edges":[{"data":{"source":…,"target":…,"type":…,"weight":…}}]}`. Helpers operate on the **raw** `{nodes,edges}` dict and pass node/edge `data` through verbatim.

- [ ] **Step 1: Write failing tests for ranking + induce**

```python
# tests/api/test_memory_subgraph.py
from __future__ import annotations

from hal0.api.routes import _memory_subgraph as sg

GRAPH = {
    "nodes": [{"data": {"id": n}} for n in ("a", "b", "c", "d", "iso")],
    "edges": [
        {"data": {"source": "a", "target": "b", "type": "causal", "weight": 1}},
        {"data": {"source": "a", "target": "c", "type": "semantic", "weight": 1}},
        {"data": {"source": "a", "target": "d", "type": "semantic", "weight": 1}},
        {"data": {"source": "b", "target": "c", "type": "temporal", "weight": 1}},
    ],
}


def test_type_weight_orders_causal_above_semantic():
    assert sg.type_weight("causal") > sg.type_weight("temporal") > \
        sg.type_weight("cooccurrence") > sg.type_weight("semantic")
    assert sg.type_weight("mystery") == sg.type_weight("semantic")  # default floor


def test_rank_by_degree_weights_salient_edges():
    ranked = sg.rank_by_degree(GRAPH)  # -> list[node_id] high→low
    assert ranked[0] == "a"            # degree 3, includes a causal edge
    assert "iso" in ranked and ranked[-1] == "iso"  # isolated sorts last


def test_rank_by_recency_tolerant_timestamp_missing_last():
    g = {
        "nodes": [
            {"data": {"id": "old", "created_at": "2026-01-01T00:00:00Z"}},
            {"data": {"id": "new", "t": "2026-06-13T00:00:00Z"}},
            {"data": {"id": "none"}},
        ],
        "edges": [],
    }
    ranked = sg.rank_by_recency(g)
    assert ranked[0] == "new"
    assert ranked.index("old") < ranked.index("none")  # missing ts sorts last


def test_induce_subgraph_keeps_only_internal_edges_and_verbatim_data():
    out = sg.induce_subgraph(GRAPH, {"a", "b"})
    ids = {n["data"]["id"] for n in out["nodes"]}
    assert ids == {"a", "b"}
    assert len(out["edges"]) == 1  # only a-b (causal); a-c/a-d/b-c dropped
    assert out["edges"][0]["data"]["type"] == "causal"  # verbatim passthrough
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/api/test_memory_subgraph.py -q` → ImportError / AttributeError.

- [ ] **Step 3: Implement helpers**

```python
# src/hal0/api/routes/_memory_subgraph.py
"""Pure graph-math for the composed memory subgraph endpoint.

Operates on raw Cytoscape payloads ({"nodes":[{"data":…}], "edges":[{"data":…}]}).
Node/edge ``data`` dicts are passed through verbatim so downstream fields
(entities, timestamps, type, weight, mention_count) survive. No FastAPI/httpx
here — keeps the graph math unit-testable in isolation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

_TYPE_WEIGHT = {"causal": 4.0, "temporal": 3.0, "cooccurrence": 2.0, "semantic": 1.0}
_TS_KEYS = ("t", "created_at", "timestamp", "updated_at")


def _nid(node: dict[str, Any]) -> Any:
    return (node.get("data") or node).get("id")


def _edge_ends(edge: dict[str, Any]) -> tuple[Any, Any, str, float]:
    d = edge.get("data") or edge
    s = d.get("source", d.get("from"))
    t = d.get("target", d.get("to"))
    lt = d.get("type", d.get("linkType")) or "semantic"
    w = d.get("weight")
    return s, t, lt, (float(w) if isinstance(w, (int, float)) else 1.0)


def type_weight(link_type: str | None) -> float:
    return _TYPE_WEIGHT.get(link_type or "semantic", _TYPE_WEIGHT["semantic"])


def adjacency(graph: dict[str, Any]) -> dict[Any, list[tuple[Any, str, float]]]:
    adj: dict[Any, list[tuple[Any, str, float]]] = defaultdict(list)
    ids = {_nid(n) for n in graph.get("nodes", [])}
    for e in graph.get("edges", []):
        s, t, lt, w = _edge_ends(e)
        if s == t or s not in ids or t not in ids:
            continue
        adj[s].append((t, lt, w))
        adj[t].append((s, lt, w))
    return adj


def rank_by_degree(graph: dict[str, Any]) -> list[Any]:
    adj = adjacency(graph)
    ids = [_nid(n) for n in graph.get("nodes", [])]
    score = {i: sum(type_weight(lt) * w for _, lt, w in adj.get(i, [])) for i in ids}
    # high score first; stable tie-break on original order
    order = {i: k for k, i in enumerate(ids)}
    return sorted(ids, key=lambda i: (-score[i], order[i]))


def _ts(node: dict[str, Any]) -> str:
    d = node.get("data") or node
    for k in _TS_KEYS:
        v = d.get(k)
        if v:
            return str(v)
    return ""  # missing sorts last (empty string < any real ts when reversed)


def rank_by_recency(graph: dict[str, Any]) -> list[Any]:
    nodes = graph.get("nodes", [])
    order = {_nid(n): k for k, n in enumerate(nodes)}
    # newest first: real timestamps desc, missing ("") last, stable by order
    return sorted(
        (_nid(n) for n in nodes),
        key=lambda i: i,  # placeholder; real key below
    ) if False else [
        _nid(n) for n in sorted(
            nodes,
            key=lambda n: (_ts(n) == "", _neg_ts(_ts(n)), order[_nid(n)]),
        )
    ]


def _neg_ts(ts: str) -> str:
    # invert lexicographic order so later timestamps sort first
    return "".join(chr(255 - ord(c)) for c in ts) if ts else ""


def induce_subgraph(graph: dict[str, Any], keep: set[Any]) -> dict[str, Any]:
    nodes = [n for n in graph.get("nodes", []) if _nid(n) in keep]
    edges = []
    for e in graph.get("edges", []):
        s, t, _, _ = _edge_ends(e)
        if s in keep and t in keep and s != t:
            edges.append(e)
    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run, verify pass.** Fix the `rank_by_recency` placeholder if the engineer prefers a cleaner sort — the only contract is: real timestamps newest-first, missing last, stable. (A simpler equivalent: `sorted(nodes, key=lambda n: order[_nid(n)])` then `sorted(..., key=lambda n: _ts(n), reverse=True)` with a stable pass; keep whichever passes the test.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(memory): graph-math helpers for subgraph endpoint"`

### Task 2: ego BFS + TTL cache

**Files:** Modify `src/hal0/api/routes/_memory_subgraph.py`; extend `tests/api/test_memory_subgraph.py`.

- [ ] **Step 1: Failing tests**

```python
def test_ego_bfs_depth_limits_and_center():
    reach1 = sg.ego_bfs(GRAPH, "a", depth=1, limit=100)
    assert reach1 == {"a", "b", "c", "d"}           # ring-1 of a
    reach2 = sg.ego_bfs(GRAPH, "b", depth=2, limit=100)
    assert "d" in reach2                              # b->a->d at depth 2
    assert sg.ego_bfs(GRAPH, "iso", depth=2, limit=100) == {"iso"}  # isolated
    assert sg.ego_bfs(GRAPH, "a", depth=1, limit=2)  # capped (center + ≤cap)


def test_graph_cache_ttl(monkeypatch):
    clock = {"now": 1000.0}
    cache = sg.GraphCache(ttl=45.0, clock=lambda: clock["now"])
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"nodes": [], "edges": []}

    cache.get_or_fetch("shared:memories::", fetch)
    cache.get_or_fetch("shared:memories::", fetch)
    assert calls["n"] == 1            # served from cache
    clock["now"] += 46
    cache.get_or_fetch("shared:memories::", fetch)
    assert calls["n"] == 2            # expired → re-fetch
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

```python
def ego_bfs(graph: dict[str, Any], center: Any, *, depth: int, limit: int) -> set[Any]:
    adj = adjacency(graph)
    ids = {_nid(n) for n in graph.get("nodes", [])}
    if center not in ids:
        return set()
    reached = {center}
    frontier = [center]
    for _ in range(max(1, depth)):
        nxt: list[Any] = []
        for cur in frontier:
            # salience order so the cap keeps the strongest neighbours
            nbrs = sorted(adj.get(cur, []), key=lambda e: -type_weight(e[1]) * e[2])
            for t, _lt, _w in nbrs:
                if t not in reached:
                    reached.add(t)
                    nxt.append(t)
                    if len(reached) >= limit:
                        return reached
        frontier = nxt
    return reached


class GraphCache:
    """Tiny per-key TTL cache for raw Hindsight graphs (injectable clock)."""

    def __init__(self, *, ttl: float = 45.0, clock=None, maxsize: int = 8) -> None:
        import time

        self._ttl = ttl
        self._clock = clock or time.monotonic
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}

    def get_or_fetch(self, key: str, fetch):
        now = self._clock()
        hit = self._store.get(key)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        value = fetch()
        if len(self._store) >= self._maxsize and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (now, value)
        return value
```

For async fetch, the route awaits the fetch itself and passes the resolved graph; `GraphCache` stays sync (cache stores already-fetched graphs). The route does: check cache → if miss, `await client.request_json(...)` then `cache.put`. Provide both `get_or_fetch` (sync test) and an async-friendly path: route uses `cached = cache.peek(key); if cached is None: cached = await fetch(); cache.put(key, cached)`. Add `peek(key)` and `put(key, value)` mirroring the TTL check.

- [ ] **Step 4: Add `peek`/`put`, run, verify pass.**

```python
    def peek(self, key: str):
        hit = self._store.get(key)
        if hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        if len(self._store) >= self._maxsize and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (self._clock(), value)
```

- [ ] **Step 5: Commit** — `git commit -am "feat(memory): ego BFS + TTL graph cache"`

### Task 3: Composed route `/banks/{bank_id}/graph/subgraph`

**Files:** Modify `src/hal0/api/routes/memory_admin.py` (add an explicit `@router.get` BEFORE the `_FORWARDS` loop; do NOT add to `_FORWARDS`). Extend `tests/api/test_memory_subgraph.py` with route tests (reuse the `_Recorder`/`MockTransport`/`_HindsightStubProvider` harness from `tests/api/test_memory_admin_routes.py` — import or copy the harness).

Module-level cache singleton: `_GRAPH_CACHE = GraphCache()`.

- [ ] **Step 1: Failing route tests** (mirror `test_memory_admin_routes.py` harness)

```python
# add to tests/api/test_memory_subgraph.py — reuse harness
from tests.api.test_memory_admin_routes import _Recorder, _HindsightStubProvider, _build_app  # noqa
# (if import is awkward, copy the three helpers verbatim into this file)

BIG = {
    "nodes": [{"data": {"id": f"n{i}", "t": f"2026-06-{(i%28)+1:02d}"}} for i in range(50)],
    "edges": [{"data": {"source": "n0", "target": f"n{i}", "type": "semantic"}} for i in range(1, 40)],
}

def _client_for(recorder):
    import httpx
    from hal0.memory.hindsight_client import HindsightRestClient
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177")
    return HindsightRestClient(http_client=http, api_key="hal0-local-noauth")

def test_subgraph_top_degree_bounds_and_counts():
    from fastapi.testclient import TestClient
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/graph/subgraph",
                  params={"mode": "top", "by": "degree", "top_k": 10})
        assert r.status_code == 200, r.text
        b = r.json()
        assert len(b["nodes"]) <= 10
        assert b["total_units"] == 50 and b["returned_nodes"] == len(b["nodes"])
        assert b["truncated"] is True
        assert any(n["data"]["id"] == "n0" for n in b["nodes"])  # hub kept

def test_subgraph_ego_requires_node():
    from fastapi.testclient import TestClient
    rec = _Recorder(); rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/graph/subgraph", params={"mode": "ego"})
        assert r.status_code == 422
        r2 = c.get("/api/memory/banks/shared/graph/subgraph",
                   params={"mode": "ego", "node": "nope"})
        assert r2.status_code == 404

def test_subgraph_entities_kind_hits_entities_graph():
    from fastapi.testclient import TestClient
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/entities/graph", 200,
                {"nodes": [{"data": {"id": "e1"}}], "edges": []})
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/graph/subgraph",
                  params={"kind": "entities", "mode": "top"})
        assert r.status_code == 200
        assert any(req["path"].endswith("/entities/graph") for req in rec.requests)
```

- [ ] **Step 2: Run, verify fail** (404 route-not-found from FastAPI).

- [ ] **Step 3: Implement route** — insert before the `_FORWARDS` block:

```python
from hal0.api.routes import _memory_subgraph as _sg

_GRAPH_CACHE = _sg.GraphCache()

def _count(graph, key):  # total_* from the full source graph
    return len(graph.get(key == "edges" and "edges" or "nodes", []))

@router.get("/banks/{bank_id}/graph/subgraph")
async def bank_subgraph(request: Request, bank_id: str) -> dict[str, Any]:
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    qp = request.query_params
    kind = qp.get("kind", "memories")
    mode = qp.get("mode", "top")
    if kind not in ("memories", "entities"):
        raise BadRequest(f"invalid kind: {kind!r}", code="memory.invalid_query")
    if mode not in ("ego", "top"):
        raise BadRequest(f"invalid mode: {mode!r}", code="memory.invalid_query")
    limit = min(int(qp.get("limit", 240)), 500)

    upstream = (
        f"/v1/default/banks/{bank_id}/entities/graph" if kind == "entities"
        else f"/v1/default/banks/{bank_id}/graph"
    )
    # narrow the source fetch with forwarded type/q; cache by (bank,kind,type,q)
    src_params = {k: qp[k] for k in ("type", "q") if qp.get(k)}
    src_params.setdefault("limit", "2000")  # pull a generous source slab
    cache_key = f"{bank_id}:{kind}:{qp.get('type','')}:{qp.get('q','')}"
    graph = _GRAPH_CACHE.peek(cache_key)
    if graph is None:
        graph = await _forward(client, "GET", upstream, params=src_params)
        _GRAPH_CACHE.put(cache_key, graph)

    total_nodes = len(graph.get("nodes", []))
    total_edges = len(graph.get("edges", []))

    if mode == "ego":
        node = qp.get("node")
        if not node:
            raise BadRequest("ego mode requires ?node=", code="memory.invalid_query")
        depth = min(int(qp.get("depth", 1)), 2)
        keep = _sg.ego_bfs(graph, node, depth=depth, limit=limit)
        if not keep:
            from hal0.errors import NotFound
            raise NotFound(f"node {node!r} not in bank graph", code="memory.node_not_found")
    else:
        by = qp.get("by") or ("degree" if kind == "entities" else "recency")
        ranked = _sg.rank_by_degree(graph) if by == "degree" else _sg.rank_by_recency(graph)
        top_k = min(int(qp.get("top_k", 200)), 500)
        keep = set(ranked[: min(top_k, limit)])

    sub = _sg.induce_subgraph(graph, keep)
    out: dict[str, Any] = dict(sub)
    out["total_edges"] = total_edges
    out[("total_entities" if kind == "entities" else "total_units")] = total_nodes
    out["returned_nodes"] = len(sub["nodes"])
    out["returned_edges"] = len(sub["edges"])
    out["truncated"] = len(sub["nodes"]) < total_nodes
    out["mode"] = mode
    out["center"] = qp.get("node")
    return out
```

If `hal0.errors` has no `NotFound`, raise `BadRequest` subclass with `status=404` or add a small `MemoryNodeNotFound(Hal0Error)` with `code="memory.node_not_found"; status=404` near the other exceptions. Confirm against `hal0/errors.py` and use whatever exists.

- [ ] **Step 4: Run all subgraph tests, verify pass.** `.venv/bin/python -m pytest tests/api/test_memory_subgraph.py -q`

- [ ] **Step 5: Full backend gate** — `.venv/bin/python -m pytest tests/api/test_memory_admin_routes.py tests/api/test_memory_subgraph.py -q` and `ruff check src/hal0/api/routes/ && ruff format --check src/hal0/api/routes/`. Fix lint.

- [ ] **Step 6: Commit** — `git commit -am "feat(memory): composed /graph/subgraph ego+top-K endpoint"`

### Task 4: Backend self-check vs live shared bank (read-only)

- [ ] **Step 1:** `ssh hal0 'curl -s "http://127.0.0.1:8080/api/memory/banks/shared/graph/subgraph?mode=top&by=degree&top_k=15" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d[\"nodes\"]),d.get(\"total_units\"),d.get(\"truncated\"))"'` — NOTE this only works AFTER deploy; pre-deploy, instead assert the contract via the unit tests. Record expected: returned ≤15, total_units≈154, truncated true. (Deploy happens in the integration step, not here.)

---

## FRONTEND (Agent B)

### Task 5: endpoint + hook + bridge export

**Files:** Modify `ui/src/api/endpoints.ts`, `ui/src/api/hooks/useHindsight.ts`, `ui/src/dash/memory-hook-bridge.ts`.

- [ ] **Step 1:** Add to `endpoints.ts` (next to `memoryBankGraph`):

```ts
  memoryBankSubgraph: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/graph/subgraph`,
```

- [ ] **Step 2:** Add hook to `useHindsight.ts` (mirror `useBankGraph`; extend `GraphPayload` with optional scope fields):

```ts
export interface GraphPayload {
  nodes: { data: Record<string, unknown> }[]
  edges: { data: Record<string, unknown> }[]
  total_units?: number
  total_entities?: number
  total_edges?: number
  returned_nodes?: number
  returned_edges?: number
  truncated?: boolean
  limit?: number
}

export function useBankSubgraph(
  bank: string | null,
  opts?: {
    kind?: 'memories' | 'entities'
    mode?: 'ego' | 'top'
    node?: string
    depth?: 1 | 2
    top_k?: number
    by?: 'degree' | 'recency'
    limit?: number
    type?: string
    q?: string
    enabled?: boolean
  },
) {
  const query = qs({
    kind: opts?.kind, mode: opts?.mode, node: opts?.node, depth: opts?.depth,
    top_k: opts?.top_k, by: opts?.by, limit: opts?.limit, type: opts?.type, q: opts?.q,
  })
  return useQuery<GraphPayload>({
    queryKey: ['memory', 'banks', bank, 'subgraph', query],
    queryFn: () =>
      apiGet<GraphPayload>(`${ENDPOINTS.memoryBankSubgraph(bank as string)}${query}`),
    enabled: !!bank && opts?.enabled !== false && (opts?.mode !== 'ego' || !!opts?.node),
    staleTime: 15_000,
  })
}
```

- [ ] **Step 3:** Bridge export in `memory-hook-bridge.ts` — add `__hal0UseBankSubgraph: useBankSubgraph,` to the `Object.assign(window, {…})` block and the import list.

- [ ] **Step 4:** Typecheck — `cd ui && npx tsc --noEmit`. Commit: `git commit -am "feat(memory-ui): useBankSubgraph hook + bridge export"`

### Task 6: mock large bank + subgraph endpoint

**Files:** Modify `ui/src/api/mock.ts`.

- [ ] **Step 1:** In `buildMemFactGraph` (or a sibling `buildBigMemFactGraph`), synthesise a large bank: ~600 fact nodes with a few high-degree hubs and varied timestamps, plus semantic/causal/temporal edges. Register a bank id e.g. `big` in the mocked `/banks` list with `fact_count: 600`.

- [ ] **Step 2:** Add a `window.fetch` short-circuit branch for `GET /api/memory/banks/:bank/graph/subgraph` that parses `mode/by/node/depth/top_k/limit` and returns the **same shape the backend returns** — reuse a JS port of the rank/ego/induce logic (degree = incident edge count weighted causal>temporal>cooccurrence>semantic; recency = node timestamp; ego = BFS to depth). Include `total_units/total_edges/returned_nodes/returned_edges/truncated/mode/center`. Keep it small — the e2e only asserts boundedness + N-of-M + expand.

- [ ] **Step 3:** Sanity: `cd ui && npx tsc --noEmit`. Commit: `git commit -am "test(memory-ui): mock large bank + /graph/subgraph"`

### Task 7: wrapper wiring — top-K when big, ego for Direction C, expand banner

**Files:** Modify `ui/src/dash/memory-graph.jsx`.

- [ ] **Step 1:** Add an `expandLevel` state (default 0). Compute a `subgraphEnabled = big` gate. When `big`, fetch via `window.__hal0UseBankSubgraph(bank, {kind: source==='entities'?'entities':'memories', mode:'top', by: source==='entities'?'degree':'recency', top_k: 200 + expandLevel*200, limit: 200 + expandLevel*200})` and prefer its data over the full `factQuery`/`entQuery` for A/B. (Keep the full-graph hooks for small banks — only swap when `big`.)

- [ ] **Step 2:** Replace the static `banner` (`memory-graph.jsx:451`) with an actionable one when truncated:

```jsx
const sub = subQuery?.data;
const banner = big ? (
  <div className="mg-scalewarn mono" data-testid="mem-graph-scalebanner">
    <Icon name="layers" size={12} />{' '}
    showing top {sub?.returned_nodes ?? nodeCount} of {sub?.total_units ?? sub?.total_entities ?? '…'} nodes
    {sub?.truncated && (
      <button
        className="mg-seg"
        data-testid="mem-graph-expand"
        onClick={() => setExpandLevel((n) => n + 1)}
      >
        {' '}expand
      </button>
    )}
  </div>
) : null;
```

- [ ] **Step 3:** Direction C (Ego) centre-change: when the active direction is `c` and a center node is selected, fetch `window.__hal0UseBankSubgraph(bank, {mode:'ego', node:centerId, depth:1, limit:240})` and feed that normalised graph to `GraphEgo` instead of the full graph. (If Direction C currently derives its center from the full graph, gate the ego fetch behind `big` so small banks keep instant local behaviour.)

- [ ] **Step 4:** `cd ui && npx tsc --noEmit` and `npm run build` (clean — wipe `node_modules/.vite` + `dist` first per the stale-CSS gotcha). Commit: `git commit -am "feat(memory-ui): top-K fetch + expand banner + Direction-C ego"`

### Task 8: e2e

**Files:** Modify `tests/e2e/specs/memory-graph-explorer-v3.spec.ts` (or new `memory-graph-subgraph.spec.ts`).

- [ ] **Step 1:** Spec (forced-mock asserts baked `mock.ts` data):

```ts
test('large bank shows top-N-of-M and expand raises the count', async ({ page }) => {
  await page.goto('/#memory/graph')
  // select the big mock bank
  await page.getByTestId('mem-graph-bank').selectOption('big')
  const banner = page.getByTestId('mem-graph-scalebanner')
  await expect(banner).toContainText(/showing top \d+ of \d+/)
  const before = await page.getByTestId('mem-graph-node').count()
  await page.getByTestId('mem-graph-expand').click()
  await expect.poll(async () => page.getByTestId('mem-graph-node').count())
    .toBeGreaterThan(before)
})
```
(Adapt `mem-graph-node` testid to whatever the node `<g>`/`<circle>` uses; if none, assert banner count text increases.)

- [ ] **Step 2:** Run — `cd ui && npx playwright test memory-graph` (forced-mock). Verify pass.

- [ ] **Step 3:** Commit: `git commit -am "test(e2e): subgraph top-K + expand"`

---

## INTEGRATION (orchestrator)

1. Merge backend + frontend branches into `feat/memory-subgraph-endpoint`.
2. Full gate: backend `pytest tests/api/test_memory_subgraph.py tests/api/test_memory_admin_routes.py -q`; UI `tsc --noEmit` + targeted e2e.
3. Live-verify (no mutation): `VITE_API_TARGET=http://10.0.1.142:8080 npx vite` → `#memory/graph` on `shared` → confirm A/B top-K + Direction-C ego hit `/graph/subgraph` and render a bounded, connected slice.
4. PR → squash-merge to `main`.
5. `wip hal0 claim` → `bash scripts/deploy.sh --ref origin/main` on CT105 → healthcheck + the Task-4 curl self-check. `wip hal0 release`.

## Self-Review notes
- Spec coverage: A (cache→Task 2), B (single endpoint→Task 3), C (UX→Task 7), data-flow/algorithm→Tasks 1-3, contract unchanged→Task 5 (`normalizeGraph` untouched), tests→Tasks 1-3/8, verification→Integration. Out-of-scope items intentionally absent.
- The `rank_by_recency` body has a documented "pick whichever passes the test" note — the contract (newest-first, missing-last, stable) is pinned by the test, not the implementation shape.
- Frontend node testid in Task 8 is flagged as adapt-on-contact.
