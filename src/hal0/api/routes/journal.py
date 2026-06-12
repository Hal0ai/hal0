"""Unified journal endpoints over hal0 events.

Issue #323 (epic #322 — Phase 1 of the journal panel rework). Surfaces
two endpoints under ``/api/journal``:

  * ``GET /api/journal`` — HTTP backfill with filter + cursor params.
  * ``GET /api/journal/stream`` — SSE live tail (with 50-entry replay).

Both serve **hal0 events** read from :class:`hal0.events.EventBus` via
:attr:`app.state.events`, flattened into the uniform
:class:`JournalEntry` the dashboard journal panel renders. Slot
containers log to journald via their ``hal0-slot@*`` units; per-slot
logs are read from journald (``/api/logs``), not through this surface.

The journal route is purposely separate from ``/api/events``: that
exposes the raw event shape for callers wanting native fidelity, while
``/api/journal`` serves the panel envelope.

No auth gate — same rationale as ``/api/events`` (post-ADR-0012 hal0-api
is open on 0.0.0.0:8080; agent identity rides on ``X-hal0-Agent``, not
Bearer tokens; the journal panel must surface during first-run before
any agent identity exists).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from hal0.events import EventBus

router = APIRouter()

# Hard ceiling on ``?limit=`` to keep a hostile caller from forcing us
# to materialise a huge response. Matches the events router's spirit.
_LIMIT_MAX = 500

# SSE keep-alive cadence. Matches the events route so a single proxy
# tuning value (>15s idle timeout) covers both streams.
_KEEPALIVE_S: float = 15.0

# Replay window for the SSE stream — backfill the last N entries
# synchronously before switching to live tail so a fresh page load
# always sees some context.
_STREAM_REPLAY_DEFAULT = 50


# ``merged`` / ``all`` are retained as aliases of the full hal0 stream
# for caller compatibility (the panel historically multiplexed sources).
SourceFilter = Literal["hal0", "merged", "all"]
LevelFilter = Literal["info", "warn", "error"]


class JournalEntry(BaseModel):
    """Unified journal row served by ``/api/journal``.

    Distinct from the raw event shape so the dashboard renders journal
    rows through one component. ``data`` carries the raw event ``type``
    + ``source`` + ``data`` so callers wanting native fidelity don't
    have to re-fetch from ``/api/events``.
    """

    id: int
    ts: str
    source: Literal["hal0"]
    level: LevelFilter
    msg: str
    data: dict[str, Any] | None = None


# ── Entry builder ─────────────────────────────────────────────────────


def _hal0_event_to_entry(event: dict[str, Any]) -> JournalEntry:
    """Project an EventBus event onto the unified ``JournalEntry`` shape.

    Severity passes through as ``level``; the event's structured
    ``type`` + ``source`` + ``data`` are carried in the entry's
    ``data`` so consumers wanting native event fidelity don't have to
    cross-reference ``/api/events``.
    """
    severity = event.get("severity") or "info"
    if severity not in {"info", "warn", "error"}:
        severity = "info"
    return JournalEntry(
        id=int(event["id"]),
        ts=str(event["ts"]),
        source="hal0",
        level=severity,  # type: ignore[arg-type]
        msg=str(event.get("message") or ""),
        data={
            "type": event.get("type"),
            "source": event.get("source"),
            **(event.get("data") or {}),
        },
    )


# ── App-state accessors ───────────────────────────────────────────────


def _bus(request: Request) -> EventBus | None:
    """Return the EventBus on app.state, or ``None`` when absent."""
    return getattr(request.app.state, "events", None)


# ── Filtering ─────────────────────────────────────────────────────────


def _passes_filters(
    entry: JournalEntry,
    *,
    level: LevelFilter | None,
    q: str | None,
) -> bool:
    """Apply level (exact) + q (case-insensitive substring on ``msg``)."""
    if level is not None and entry.level != level:
        return False
    return not (q and q.lower() not in entry.msg.lower())


def _collect(
    request: Request,
    *,
    since: int | None,
) -> list[JournalEntry]:
    """Pull raw entries from the event bus, no filter applied."""
    entries: list[JournalEntry] = []
    bus = _bus(request)
    if bus is not None:
        for ev in bus.backfill(since=since, limit=_LIMIT_MAX):
            entries.append(_hal0_event_to_entry(ev))
    return entries


def _sort_and_clamp(entries: list[JournalEntry], limit: int) -> list[JournalEntry]:
    """Sort entries by ``ts`` ascending, keep the newest ``limit``."""
    entries.sort(key=lambda e: e.ts)
    if len(entries) > limit:
        entries = entries[-limit:]
    return entries


# ── HTTP backfill ─────────────────────────────────────────────────────


@router.get("")
async def get_journal(
    request: Request,
    source: SourceFilter = Query(default="merged"),
    level: LevelFilter | None = Query(default=None),
    q: str | None = Query(default=None),
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=_LIMIT_MAX),
) -> dict[str, Any]:
    """Return a page of journal entries with a cursor for the next call.

    ``next_since`` is the **largest id seen** in the returned page —
    callers should pass it back as ``since`` to receive deltas.
    """
    _ = source  # all source values resolve to the hal0 event stream
    raw = _collect(request, since=since)
    filtered = [e for e in raw if _passes_filters(e, level=level, q=q)]
    page = _sort_and_clamp(filtered, limit)
    if page:
        next_since: int | None = max(e.id for e in page)
    else:
        next_since = since
    return {
        "entries": [e.model_dump() for e in page],
        "next_since": next_since,
    }


# ── SSE live tail ─────────────────────────────────────────────────────


async def _stream_iter(
    request: Request,
    *,
    level: LevelFilter | None,
    q: str | None,
    since: int | None,
) -> Any:
    """Async generator producing SSE frames for ``/api/journal/stream``.

    1. Subscribe up-front so events emitted between backfill snapshot
       and live tail don't get dropped.
    2. Yield a synchronous backfill of the last ~50 entries.
    3. Tail live entries off the subscriber queue, relabelling into the
       unified envelope as they arrive.
    """
    bus = _bus(request)

    if bus is None:
        # No event bus (unit-test fixture) — degrade to keep-alives.
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(_KEEPALIVE_S)
            yield ": keepalive\n\n"

    async with bus.subscribe() as queue:
        # ── Replay ────────────────────────────────────────────────────
        raw = _collect(request, since=since)
        filtered = [e for e in raw if _passes_filters(e, level=level, q=q)]
        replay = _sort_and_clamp(filtered, _STREAM_REPLAY_DEFAULT)
        last_id = since or 0
        for entry in replay:
            yield f"data: {json.dumps(entry.model_dump())}\n\n"
            last_id = max(last_id, entry.id)

        # ── Live tail ─────────────────────────────────────────────────
        while True:
            if await request.is_disconnected():
                return
            try:
                raw_entry = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_S)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            entry = _hal0_event_to_entry(raw_entry)
            if entry.id <= last_id:
                continue
            last_id = entry.id
            if not _passes_filters(entry, level=level, q=q):
                continue
            yield f"data: {json.dumps(entry.model_dump())}\n\n"


@router.get("/stream")
async def stream_journal(
    request: Request,
    source: SourceFilter = Query(default="merged"),
    level: LevelFilter | None = Query(default=None),
    q: str | None = Query(default=None),
    since: int | None = Query(default=None, ge=0),
) -> StreamingResponse:
    """SSE live tail of the journal.

    Replays the last ~50 filtered entries synchronously, then streams
    live additions until the client disconnects. Keep-alive comment
    frames pulse every 15s so proxies don't reap idle connections.
    """
    _ = source  # all source values resolve to the hal0 event stream

    async def _safe() -> Any:
        try:
            async for chunk in _stream_iter(
                request,
                level=level,
                q=q,
                since=since,
            ):
                yield chunk
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        _safe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


__all__ = [
    "JournalEntry",
    "router",
]
