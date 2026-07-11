"""PgVectorProvider — the documented boot fallback (spec P1 degrade ladder).

Minimal MemoryProvider impl with the same shared+own-private ACL behaviour
as the engines. Stands in when Hindsight is unavailable at boot so the
tools return empties + the dashboard shows "no engine" instead of crashing.
A real pgvector backing is deferred; the contract + degrade path are what P0
needs.

SAFETY: this provider is IN-MEMORY ONLY — all writes are LOST on restart.
Callers can detect this via the ``degraded`` attribute (always ``True`` here).
``add()`` emits a structlog WARNING on the first write per instance to alert
operators before data loss occurs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from hal0.memory.provider import MemoryProvider

log = structlog.get_logger(__name__)

_SHARED = "shared"
_PRIVATE = "private:"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PgVectorProvider(MemoryProvider):
    #: Always True — signals that this provider is the in-memory degrade
    #: fallback; writes are NOT persisted and are LOST on restart.
    degraded: bool = True

    def __init__(self, *, client_id: str = "anonymous") -> None:
        self._client_id = client_id
        self._rows: list[dict[str, Any]] = []
        self._graph_enabled = False
        self._extraction_slot = "utility"
        self._rerank_enabled = False
        # Emit the degrade warning exactly once per provider instance so log
        # consumers see it on construction rather than being spammed on each
        # write.  A second warning fires on the first add() call so the
        # WARNING is visible in the write path even when the provider was
        # constructed outside of our factory (e.g. in tests).
        self._add_warned = False
        log.warning(
            "hal0.memory.degraded_provider_active",
            detail=(
                "Memory is running on the in-memory PgVectorProvider fallback. "
                "All writes are VOLATILE and will be LOST on restart. "
                "Ensure Hindsight is reachable to enable durable storage."
            ),
        )

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
        # Emit a per-call warning (throttled to once per instance) so the
        # write-path is loud even when the construction-time warning was
        # swallowed by a log filter or the provider was built outside our
        # factory.
        if not self._add_warned:
            self._add_warned = True
            log.warning(
                "hal0.memory.degraded_write",
                detail=(
                    "Memory write accepted by in-memory PgVectorProvider. "
                    "This data is VOLATILE and will be LOST on restart."
                ),
                dataset=dataset,
            )
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
