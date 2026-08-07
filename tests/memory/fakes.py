"""In-memory MemoryProvider for the default-gate conformance suite.

Honors the ACL contract (shared + own-private reads; foreign-private reads
fail-open-empty) with zero external deps so the suite runs on every PR.
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


class FakeMemoryProvider(MemoryProvider):
    def __init__(self, *, client_id: str = "anonymous") -> None:
        self._client_id = client_id
        self._rows: list[dict[str, Any]] = []
        self._graph_enabled = False
        self._extraction_slot = "utility"
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
                continue  # foreign private — dropped (fail-open-empty)
            elif ds not in out:
                out.append(ds)
        return out

    async def add(
        self, text, dataset=_SHARED, tags=None, source=None, metadata=None, client_id=None
    ):
        item_id = str(uuid.uuid4())
        self._rows.append(
            {
                "id": item_id,
                "text": text,
                "timestamp": _now(),
                "dataset": dataset,
                "tags": list(tags or []),
                "source": source or (client_id or self._client_id),
                "metadata": dict(metadata or {}),
                "score": None,
            }
        )
        return {"id": item_id, "timestamp": self._rows[-1]["timestamp"]}

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
        tag_groups=None,
        min_scores=None,
    ):
        del tag_groups, min_scores  # accepted for ABC compliance; unused by this fake
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
        items = [dict(r) for r in self._rows if r["dataset"] in allowed][:limit]
        return {"items": items, "next_cursor": None}

    async def delete(self, ids, *, client_id=None, dataset=None):
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["id"] not in set(ids)]
        return {"deleted": before - len(self._rows)}

    def graph_status(self):
        return {
            "enabled": self._graph_enabled,
            "extraction_slot": self._extraction_slot,
            "route": self._extraction_slot,  # deprecated mirror (ADR-0023)
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled, extraction_slot=None):
        self._graph_enabled = bool(enabled)
        if extraction_slot is not None:
            self._extraction_slot = extraction_slot

    def set_rerank_enabled(self, enabled):
        self._rerank_enabled = bool(enabled)
