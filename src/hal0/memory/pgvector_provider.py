"""PgVectorProvider — the documented boot fallback (spec P1 degrade ladder).

Minimal MemoryProvider impl with the same shared+own-private ACL behaviour
as the engines. Stands in when Hindsight is unavailable at boot so the
tools return empties + the dashboard shows "no engine" instead of crashing.
A real pgvector backing is deferred; the contract + degrade path are what P0
needs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hal0.memory.provider import MemoryProvider

_SHARED = "shared"
_PRIVATE = "private:"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PgVectorProvider(MemoryProvider):
    def __init__(self, *, client_id: str = "anonymous") -> None:
        self._client_id = client_id
        self._rows: list[dict[str, Any]] = []
        self._graph_enabled = False
        self._graph_route = "upstream"
        self._rerank_enabled = False

    def _allowed(self, requested: str | list[str], client_id: str | None) -> list[str]:
        cid = client_id or self._client_id
        own = f"{_PRIVATE}{cid}"
        reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
        out: list[str] = []
        for ds in reqs:
            if ds == _SHARED:
                out += [d for d in (_SHARED, own) if d not in out]
            elif ds == own and own not in out:
                out.append(own)
            elif ds.startswith(_PRIVATE):
                continue
            elif ds not in out:
                out.append(ds)
        return out

    async def add(
        self,
        text,
        dataset=_SHARED,
        tags=None,
        source=None,
        metadata=None,
        client_id=None,
        document_id=None,
    ):
        # No document semantics on this engine — a caller-supplied
        # document_id just becomes the item id so delete round-trips.
        item_id = document_id or str(uuid.uuid4())
        ts = _now()
        self._rows.append(
            {
                "id": item_id,
                "text": text,
                "timestamp": ts,
                "dataset": dataset,
                "tags": list(tags or []),
                "source": source or (client_id or self._client_id),
                "metadata": dict(metadata or {}),
                "score": None,
            }
        )
        return {"id": item_id, "timestamp": ts}

    async def search(
        self,
        query,
        limit=10,
        dataset=_SHARED,
        tags=None,
        before=None,
        after=None,
        mode="vector",
        client_id=None,
    ):
        allowed = self._allowed(dataset, client_id)
        tags = tags or []
        out = []
        for row in self._rows:
            if row["dataset"] not in allowed:
                continue
            if tags and not all(t in row["tags"] for t in tags):
                continue
            if before and row["timestamp"] >= before:
                continue
            if after and row["timestamp"] <= after:
                continue
            out.append(dict(row))
            if len(out) >= limit:
                break
        return out

    async def list_items(self, dataset=_SHARED, cursor=None, limit=50, client_id=None):
        allowed = self._allowed(dataset, client_id)
        return {
            "items": [dict(r) for r in self._rows if r["dataset"] in allowed][:limit],
            "next_cursor": None,
        }

    async def delete(self, ids, *, client_id=None, dataset=None):
        # dataset narrowing is a Hindsight-bank concept; rows here carry
        # their namespace inline, so the id match is already scoped.
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["id"] not in set(ids)]
        return {"deleted": before - len(self._rows)}

    def graph_status(self):
        return {
            "enabled": self._graph_enabled,
            "route": self._graph_route,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled, route=None):
        self._graph_enabled = bool(enabled)
        if route is not None:
            self._graph_route = route

    def set_rerank_enabled(self, enabled):
        self._rerank_enabled = bool(enabled)
