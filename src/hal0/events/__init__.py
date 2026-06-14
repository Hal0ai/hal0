"""In-process event bus + ring buffer for the dashboard footer.

The footer status bar subscribes to a single fan-out stream of structured
events:

    Event = {
        "id":       int,           # monotonic, assigned at emit
        "ts":       str,           # ISO-8601 UTC
        "type":     str,           # dotted, e.g. "slot.state" or "pull.progress"
        "severity": "info"|"warn"|"error",
        "source":   str,           # subsystem identifier, e.g. "slot:primary"
        "message":  str,           # human-readable one-liner
        "data":     dict,          # opaque structured payload
    }

State lives on ``app.state.events`` so route handlers + background tasks
can `await events.emit(...)` without coupling to FastAPI internals. The
ring caches the last 500 events for SSE replay-on-reconnect; per-subscriber
queues fan-out live events with a bounded backlog (we drop oldest on a
slow consumer rather than blocking the producer).
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import itertools
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

__all__ = ["EventBus", "Severity", "make_event"]

_log = logging.getLogger("hal0.events")

Severity = str  # "info" | "warn" | "error" — kept loose to avoid Literal import noise

_SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2}

# Ring + per-subscriber queue caps. Ring is sized for ~1 min of bursty
# pull progress chunks; per-queue cap is smaller — slow consumers get
# their oldest entries dropped (see ``EventBus.emit``).
_RING_MAXLEN = 500
_SUBSCRIBER_MAXSIZE = 256


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat()


def make_event(
    event_id: int,
    *,
    type: str,
    severity: Severity,
    source: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical event dict. Exposed for tests + manual injection."""
    return {
        "id": event_id,
        "ts": _now_iso(),
        "type": type,
        "severity": severity,
        "source": source,
        "message": message,
        "data": dict(data) if data else {},
    }


class EventBus:
    """Fan-out event bus with a bounded ring buffer.

    Thread-safety: all methods are intended to be called from a single
    asyncio event loop. ``emit`` is synchronous (the underlying queue
    ``put_nowait`` never awaits); we expose it as ``async`` so future
    enrichment (e.g. forwarding to journald) can await without churning
    every call site.
    """

    def __init__(
        self,
        *,
        ring_maxlen: int = _RING_MAXLEN,
        subscriber_maxsize: int = _SUBSCRIBER_MAXSIZE,
        sink: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self.ring: deque[dict[str, Any]] = deque(maxlen=ring_maxlen)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._next_id: itertools.count[int] = itertools.count(1)
        self._subscriber_maxsize = subscriber_maxsize
        # Optional durable forwarder (the "future enrichment" hook noted in
        # the class docstring). When set, every emitted event is also handed
        # to ``sink`` — e.g. the AuditStore, which persists it. A sink that
        # raises must never break emit, so we swallow + log its failures.
        self._sink = sink

    # ── Emit ──────────────────────────────────────────────────────────

    async def emit(
        self,
        type: str,
        severity: Severity,
        source: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an event to the ring and fan it out to every subscriber.

        Never blocks. If a subscriber queue is full we drop its oldest
        entry to make room — a stuck consumer cannot stall the producer.
        Returns the materialised event dict so callers can log it.
        """
        event = make_event(
            next(self._next_id),
            type=type,
            severity=severity,
            source=source,
            message=message,
            data=data,
        )
        self.ring.append(event)
        # Snapshot the set so a concurrent unsubscribe during iteration
        # (subscribers exit on task cancel) doesn't raise RuntimeError.
        for q in list(self.subscribers):
            self._enqueue(q, event)
        if self._sink is not None:
            try:
                await self._sink(event)
            except Exception:
                _log.warning("events.sink_failed", exc_info=True)
        return event

    def _enqueue(self, q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        """Push to a subscriber queue, dropping oldest on overflow."""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Drop one to free a slot, then retry. ``get_nowait`` can
            # itself raise QueueEmpty in a tight race (another consumer
            # drained it between the full + get) — fall through silently
            # if so; the next emit will try again.
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    # ── Subscribe ─────────────────────────────────────────────────────

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Yield an asyncio.Queue receiving every event emitted after entry.

        Use as ``async with bus.subscribe() as q: ...``. The queue is
        unregistered on exit (including exceptions) so disconnected SSE
        clients don't leak slots.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._subscriber_maxsize)
        self.subscribers.add(q)
        try:
            yield q
        finally:
            self.subscribers.discard(q)

    # ── Backfill ──────────────────────────────────────────────────────

    def backfill(
        self,
        since: int | None = None,
        type_glob: str | None = None,
        min_severity: Severity | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return matching ring entries with id > since, ordered ascending.

        Filters are AND-composed: an event must satisfy every supplied
        predicate. ``type_glob`` uses fnmatch (``slot.*``, ``pull.*``).
        ``min_severity`` is inclusive — "warn" returns warn + error.
        ``limit`` clamps the result; the oldest matches are dropped first
        so SSE replay-on-reconnect always sees the most recent context.
        """
        if limit <= 0:
            return []
        min_rank = _SEVERITY_ORDER.get(min_severity, -1) if min_severity else -1
        out: list[dict[str, Any]] = []
        for ev in self.ring:
            if since is not None and ev["id"] <= since:
                continue
            if type_glob and not fnmatch.fnmatchcase(ev["type"], type_glob):
                continue
            if min_rank >= 0 and _SEVERITY_ORDER.get(ev["severity"], 0) < min_rank:
                continue
            out.append(ev)
        if len(out) > limit:
            out = out[-limit:]
        return out
