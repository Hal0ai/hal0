"""Pure graph-math for the composed memory subgraph endpoint.

Operates on raw Cytoscape payloads ({"nodes":[{"data":…}], "edges":[{"data":…}]}).
Node/edge ``data`` dicts are passed through verbatim so downstream fields
(entities, timestamps, type, weight, mention_count) survive. No FastAPI/httpx
here — keeps the graph math unit-testable in isolation.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
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
    # primary: raw incident-edge count (degree); secondary: weighted salience so
    # equal-degree nodes with stronger (causal>…>semantic) edges rank higher;
    # final: original order for stability.
    deg = {i: len(adj.get(i, [])) for i in ids}
    sal = {i: sum(type_weight(lt) * w for _, lt, w in adj.get(i, [])) for i in ids}
    order = {i: k for k, i in enumerate(ids)}
    return sorted(ids, key=lambda i: (-deg[i], -sal[i], order[i]))


def _ts(node: dict[str, Any]) -> str:
    d = node.get("data") or node
    for k in _TS_KEYS:
        v = d.get(k)
        if v:
            return str(v)
    return ""  # missing sorts last


def rank_by_recency(graph: dict[str, Any]) -> list[Any]:
    nodes = graph.get("nodes", [])
    order = {_nid(n): k for k, n in enumerate(nodes)}
    # newest first: real timestamps desc, missing ("") last, stable by order
    return [
        _nid(n)
        for n in sorted(
            nodes,
            key=lambda n: (_ts(n) == "", _neg_ts(_ts(n)), order[_nid(n)]),
        )
    ]


def _neg_ts(ts: str) -> str:
    # invert lexicographic order so later timestamps sort first
    return "".join(chr(0x10FFFF - ord(c)) for c in ts) if ts else ""


def induce_subgraph(graph: dict[str, Any], keep: set[Any]) -> dict[str, Any]:
    nodes = [n for n in graph.get("nodes", []) if _nid(n) in keep]
    edges = []
    for e in graph.get("edges", []):
        s, t, _, _ = _edge_ends(e)
        if s in keep and t in keep and s != t:
            edges.append(e)
    return {"nodes": nodes, "edges": edges}


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

    def __init__(
        self,
        *,
        ttl: float = 45.0,
        clock: Callable[[], float] | None = None,
        maxsize: int = 8,
    ) -> None:
        self._ttl = ttl
        self._clock = clock or time.monotonic
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}

    def _evict_if_full(self, key: str) -> None:
        if len(self._store) >= self._maxsize and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)

    def get_or_fetch(self, key: str, fetch: Callable[[], Any]) -> Any:
        hit = self.peek(key)
        if hit is not None:
            return hit
        value = fetch()
        self.put(key, value)
        return value

    def peek(self, key: str) -> Any | None:
        hit = self._store.get(key)
        if hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        self._evict_if_full(key)
        self._store[key] = (self._clock(), value)
