"""Lemond log ring + fan-out for the journal panel (issue #323, epic #322).

The journal panel needs a unified view of two log streams:

  * **hal0 events** — already buffered by :class:`hal0.events.EventBus`
    (500-entry ring + per-subscriber fan-out).
  * **lemond logs** — surfaced today as a passthrough SSE in
    :mod:`hal0.api.routes.lemonade_logs`, but with no in-process backfill:
    a freshly-loaded panel sees only events emitted *after* it subscribes.

This module fills the lemond-side gap. It mirrors :class:`hal0.events.EventBus`'s
shape (bounded ring + per-subscriber asyncio.Queue with drop-oldest overflow)
so the journal route can compose both surfaces uniformly. A single background
task driven from the FastAPI lifespan keeps the ring fed; the task is
resilient to lemond bouncing — it reconnects with exponential backoff so a
restart of lemond doesn't permanently silence the journal.

Why not just reuse :class:`EventBus` directly? The two surfaces have
distinct id namespaces (EventBus events vs lemond lines) and disjoint
filter semantics (severity glob vs lemond ``level`` strings). Coupling
them risks accidentally fanning hal0 events into the lemond ring on a
careless refactor. A small dedicated ring keeps the boundary explicit.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog

__all__ = ["LemondLogRing", "now_iso", "start_lemond_bridge"]

log = structlog.get_logger(__name__)

# Match EventBus sizing — 500 entries is roughly a minute of bursty
# lemond load/unload chatter and keeps memory tiny (~250KB worst case).
_RING_MAXLEN = 500
_SUBSCRIBER_MAXSIZE = 256

# Reconnect backoff bounds for the lemond bridge. Start fast so a one-off
# restart of lemond catches back up within a couple of seconds; cap at
# 30s so a permanently-down lemond doesn't churn the event loop.
_RECONNECT_INITIAL_S = 1.0
_RECONNECT_MAX_S = 30.0


def now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat()


class LemondLogRing:
    """Bounded ring + fan-out for lemond log entries.

    Mirrors :class:`hal0.events.EventBus` but keyed on a private id
    counter so consumers can cursor-paginate independently of the
    EventBus id space.

    Each stored entry is a dict::

        {
            "id":      int,                # monotonic, assigned on append
            "ts":      str,                # ISO-8601 UTC (lemond's if present)
            "level":   "info"|"warn"|"error",
            "message": str,
            "raw":     dict,               # original lemond payload, opaque
        }
    """

    def __init__(
        self,
        *,
        ring_maxlen: int = _RING_MAXLEN,
        subscriber_maxsize: int = _SUBSCRIBER_MAXSIZE,
    ) -> None:
        self.ring: deque[dict[str, Any]] = deque(maxlen=ring_maxlen)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._next_id: itertools.count[int] = itertools.count(1)
        self._subscriber_maxsize = subscriber_maxsize

    # ── Append ────────────────────────────────────────────────────────

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Append a normalised lemond entry to the ring + fan out.

        Accepts the raw lemond payload (``{"message": ..., "level": ...,
        "ts": ...}`` with the documented ``text|msg|line`` fallbacks) and
        normalises it into the canonical shape above. Returns the stored
        dict so callers (and tests) can inspect the assigned id.
        """
        message = _pick_message(entry)
        level = _normalise_level(entry.get("level"))
        ts_raw = entry.get("ts")
        ts = ts_raw if isinstance(ts_raw, str) and ts_raw else now_iso()
        stored = {
            "id": next(self._next_id),
            "ts": ts,
            "level": level,
            "message": message,
            "raw": dict(entry),
        }
        self.ring.append(stored)
        for q in list(self.subscribers):
            self._enqueue(q, stored)
        return stored

    def _enqueue(self, q: asyncio.Queue[dict[str, Any]], entry: dict[str, Any]) -> None:
        """Push to a subscriber queue, dropping oldest on overflow."""
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(entry)

    # ── Subscribe ─────────────────────────────────────────────────────

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Yield an asyncio.Queue receiving every entry appended after entry.

        Use as ``async with ring.subscribe() as q: ...``. The queue is
        unregistered on context exit, including exceptions, so disconnected
        SSE clients don't leak subscriber slots.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._subscriber_maxsize)
        self.subscribers.add(q)
        try:
            yield q
        finally:
            self.subscribers.discard(q)

    # ── Backfill ──────────────────────────────────────────────────────

    def backfill(self, since: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Return matching ring entries with id > since, ordered ascending.

        ``limit`` clamps to the most-recent matches so a long-disconnected
        client picks up where the ring still has context rather than seeing
        a truncated head.
        """
        if limit <= 0:
            return []
        out: list[dict[str, Any]] = []
        for entry in self.ring:
            if since is not None and entry["id"] <= since:
                continue
            out.append(entry)
        if len(out) > limit:
            out = out[-limit:]
        return out


# ── Lemond payload normalisation ───────────────────────────────────────


_LEVEL_ALIASES = {
    "info": "info",
    "information": "info",
    "debug": "info",
    "trace": "info",
    "warn": "warn",
    "warning": "warn",
    "err": "error",
    "error": "error",
    "fatal": "error",
    "critical": "error",
}


def _pick_message(entry: dict[str, Any]) -> str:
    """Extract the human-readable log line from a lemond entry.

    Mirrors :func:`hal0.api.routes.lemonade_logs._extract_message` but
    folded here so the ring module has no cross-route dependency. Same
    fallback order: ``message`` → ``text`` → ``msg`` → ``line``.
    """
    msg = entry.get("message")
    if isinstance(msg, str):
        return msg
    for alt in ("text", "msg", "line"):
        v = entry.get(alt)
        if isinstance(v, str):
            return v
    return ""


def _normalise_level(value: Any) -> str:
    """Map lemond's varied level strings onto info|warn|error.

    Unknown / missing values default to ``info`` — surfacing them as
    ``error`` would force the dashboard to colour every untyped line
    red. ``info`` is the safe default; operators who care can filter
    by message content.
    """
    if not isinstance(value, str):
        return "info"
    return _LEVEL_ALIASES.get(value.strip().lower(), "info")


# ── Background bridge: lemond WS → ring ────────────────────────────────


async def _flatten_frame(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a lemond log frame into per-entry dicts.

    Inline duplicate of :func:`hal0.api.routes.lemonade_logs._iter_entries`
    so this module stays import-free of the route layer. Tolerates the
    same ``logs.snapshot`` / ``logs.entry`` / unknown-op shapes.
    """
    op = frame.get("op")
    if op == "logs.snapshot":
        entries = frame.get("entries")
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
        return []
    if op == "logs.entry":
        entry = frame.get("entry")
        if isinstance(entry, dict):
            return [entry]
        return []
    return [frame]


async def _consume_once(ring: LemondLogRing) -> None:
    """One pass through lemond's log stream. Returns when the stream ends.

    Imports the provider lazily so unit tests can monkeypatch it on the
    way in without dragging the full provider singleton into scope.
    """
    from hal0.providers import lemonade_provider

    client = lemonade_provider().client()
    async for frame in client.stream_logs():
        for entry in await _flatten_frame(frame):
            ring.append(entry)


async def _bridge_loop(ring: LemondLogRing) -> None:
    """Long-running task that keeps the lemond log ring fed.

    Reconnects with exponential backoff (1s → 30s) on any failure so the
    journal panel keeps working across lemond restarts. Cancellation
    (FastAPI shutdown) propagates cleanly via :class:`asyncio.CancelledError`.
    """
    backoff = _RECONNECT_INITIAL_S
    while True:
        try:
            await _consume_once(ring)
            # Stream returned cleanly — reset backoff so the next reconnect
            # is fast (typically lemond restart, not a permanent outage).
            backoff = _RECONNECT_INITIAL_S
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "journal.lemond_bridge_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                backoff_s=backoff,
            )
        # Sleep before reconnecting. A clean stream return still backs off
        # by the initial 1s — without it, a lemond that immediately closes
        # the WS would spin the event loop.
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            raise
        backoff = min(backoff * 2, _RECONNECT_MAX_S)


def start_lemond_bridge(ring: LemondLogRing) -> asyncio.Task[None]:
    """Spawn the background task that forwards lemond logs into ``ring``.

    Caller (the FastAPI lifespan) is responsible for cancelling the task
    on shutdown. The returned task survives lemond bouncing thanks to
    :func:`_bridge_loop`'s reconnect logic; cancellation is the only way
    it exits.
    """
    return asyncio.create_task(_bridge_loop(ring))
