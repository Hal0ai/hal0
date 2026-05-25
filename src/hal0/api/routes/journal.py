"""Unified journal endpoints — merges hal0 events + lemond logs.

Issue #323 (epic #322 — Phase 1 of the journal panel rework). Surfaces
two endpoints under ``/api/journal``:

  * ``GET /api/journal`` — HTTP backfill with filter + cursor params.
  * ``GET /api/journal/stream`` — SSE live tail (with 50-entry replay).

Both endpoints compose two upstream surfaces into one unified shape:

  * **hal0 events** read from :class:`hal0.events.EventBus` via
    :attr:`app.state.events`.
  * **lemond logs** read from :class:`hal0.journal.LemondLogRing` via
    :attr:`app.state.lemond_log_ring` (populated by the lifespan-owned
    bridge task — see :mod:`hal0.api.__init__`).

The journal route is purposely separate from the existing
``/api/events`` + ``/api/lemonade/logs/stream`` surfaces: those expose
the raw single-source shapes for callers that want native fidelity,
while ``/api/journal`` flattens both into a uniform :class:`JournalEntry`
the dashboard journal panel can render without per-source branching.

No auth gate — same rationale as ``/api/events`` (post-ADR-0012 hal0-api
is open on 0.0.0.0:8080; agent identity rides on ``X-hal0-Agent``, not
Bearer tokens; the journal panel must surface during first-run before
any agent identity exists).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from hal0.events import EventBus
from hal0.journal import LemondLogRing, now_iso

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


SourceFilter = Literal["hal0", "lemond", "merged", "all"]
LevelFilter = Literal["info", "warn", "error"]


class JournalEntry(BaseModel):
    """Unified journal row served by ``/api/journal``.

    Distinct from the raw event shape so the dashboard can render hal0
    events and lemond log lines through one component without per-source
    branching. ``data`` is opaque — for hal0 events it carries the raw
    event ``type`` + ``source`` + ``data`` so callers wanting native
    fidelity don't have to re-fetch from ``/api/events``; for lemond it
    carries the raw payload so a future panel-side detail expander has
    something to show.
    """

    id: int
    ts: str
    source: Literal["hal0", "lemond"]
    level: LevelFilter
    msg: str
    data: dict[str, Any] | None = None


# ── Source-specific entry builders ────────────────────────────────────


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


def _lemond_entry_to_entry(stored: dict[str, Any]) -> JournalEntry:
    """Project a stored lemond ring entry onto ``JournalEntry``.

    The ring already normalised ``level`` + ``ts`` + ``message`` on
    append (see :class:`LemondLogRing.append`); we just relabel into
    the journal envelope here.
    """
    level = stored.get("level") or "info"
    if level not in {"info", "warn", "error"}:
        level = "info"
    return JournalEntry(
        id=int(stored["id"]),
        ts=str(stored.get("ts") or now_iso()),
        source="lemond",
        level=level,  # type: ignore[arg-type]
        msg=str(stored.get("message") or ""),
        data=dict(stored.get("raw") or {}) or None,
    )


# ── App-state accessors ───────────────────────────────────────────────


def _bus(request: Request) -> EventBus | None:
    """Return the EventBus on app.state, or ``None`` when absent."""
    return getattr(request.app.state, "events", None)


def _lemond_ring(request: Request) -> LemondLogRing | None:
    """Return the lemond log ring on app.state, or ``None`` when absent."""
    return getattr(request.app.state, "lemond_log_ring", None)


# ── Filtering ─────────────────────────────────────────────────────────


def _passes_filters(
    entry: JournalEntry,
    *,
    level: LevelFilter | None,
    q: str | None,
) -> bool:
    """Apply level (exact) + q (case-insensitive substring on ``msg``).

    Source filtering happens at collection time (we skip the source
    entirely when ``source != "merged"|"all"``), so it's not handled
    here.
    """
    if level is not None and entry.level != level:
        return False
    return not (q and q.lower() not in entry.msg.lower())


def _collect(
    request: Request,
    *,
    source: SourceFilter,
    since: int | None,
) -> list[JournalEntry]:
    """Pull raw entries from the selected sources, no filter applied.

    Returns the materialised :class:`JournalEntry` list. ``since``
    cursors each source independently — the EventBus + lemond ring
    have disjoint id namespaces, so a single ``since`` value can't
    cleanly span both. Phase 2 introduces compound cursors; today the
    cursor applies per-source and the merge runs over the union.
    """
    entries: list[JournalEntry] = []
    if source in {"hal0", "merged", "all"}:
        bus = _bus(request)
        if bus is not None:
            for ev in bus.backfill(since=since, limit=_LIMIT_MAX):
                entries.append(_hal0_event_to_entry(ev))
    if source in {"lemond", "merged", "all"}:
        ring = _lemond_ring(request)
        if ring is not None:
            for stored in ring.backfill(since=since, limit=_LIMIT_MAX):
                entries.append(_lemond_entry_to_entry(stored))
    return entries


def _sort_and_clamp(entries: list[JournalEntry], limit: int) -> list[JournalEntry]:
    """Sort merged entries by ``ts`` ascending, keep the newest ``limit``."""
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
    """Return a page of unified journal entries with a cursor for the next call.

    ``next_since`` is the **largest id seen** in the returned page —
    callers should pass it back as ``since`` to receive deltas. Because
    the two sources share a single id namespace from the caller's
    perspective (the dashboard polls one endpoint), a small ambiguity
    arises: an id-N hal0 event and an id-N lemond log are both > since-N.
    Today this means a polling caller might re-see one entry on the
    next call. Phase 2's compound-cursor work resolves it; the cost
    of a single duplicate per poll is acceptable.
    """
    raw = _collect(request, source=source, since=since)
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
    source: SourceFilter,
    level: LevelFilter | None,
    q: str | None,
    since: int | None,
) -> Any:
    """Async generator producing SSE frames for ``/api/journal/stream``.

    1. Subscribe to both sources up-front so events emitted between
       backfill snapshot and live tail don't get dropped.
    2. Yield a synchronous backfill of the last ~50 entries.
    3. Multiplex live entries off whichever subscriber queue fires next,
       relabelling into the unified envelope as they arrive.
    """
    bus = _bus(request)
    ring = _lemond_ring(request)

    # Build subscription contexts for whichever sources are selected.
    # We use AsyncExitStack so cancellation cleans up both queues even
    # when one source is absent (e.g. lemond bridge not started in a
    # unit-test fixture).
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        hal0_q: asyncio.Queue[dict[str, Any]] | None = None
        lemond_q: asyncio.Queue[dict[str, Any]] | None = None

        if source in {"hal0", "merged", "all"} and bus is not None:
            hal0_q = await stack.enter_async_context(bus.subscribe())
        if source in {"lemond", "merged", "all"} and ring is not None:
            lemond_q = await stack.enter_async_context(ring.subscribe())

        # ── Replay ────────────────────────────────────────────────────
        replay_limit = _STREAM_REPLAY_DEFAULT
        raw = _collect(request, source=source, since=since)
        filtered = [e for e in raw if _passes_filters(e, level=level, q=q)]
        replay = _sort_and_clamp(filtered, replay_limit)
        last_hal0_id = since or 0
        last_lemond_id = since or 0
        for entry in replay:
            yield f"data: {json.dumps(entry.model_dump())}\n\n"
            if entry.source == "hal0":
                last_hal0_id = max(last_hal0_id, entry.id)
            else:
                last_lemond_id = max(last_lemond_id, entry.id)

        # ── Live tail ─────────────────────────────────────────────────
        # asyncio.wait on the per-source queue.get() coroutines so a
        # single loop iteration drains whichever surface fired. The
        # keep-alive timer is folded into the same wait so we don't
        # need a third coroutine.
        while True:
            if await request.is_disconnected():
                return

            pending: dict[asyncio.Task[Any], str] = {}
            if hal0_q is not None:
                t = asyncio.create_task(hal0_q.get())
                pending[t] = "hal0"
            if lemond_q is not None:
                t = asyncio.create_task(lemond_q.get())
                pending[t] = "lemond"

            if not pending:
                # No active sources — degrade gracefully by sleeping the
                # keep-alive interval and emitting a comment frame.
                await asyncio.sleep(_KEEPALIVE_S)
                yield ": keepalive\n\n"
                continue

            done, still_pending = await asyncio.wait(
                pending.keys(),
                timeout=_KEEPALIVE_S,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the losers so their queue.get() doesn't leak a
            # consumer between iterations.
            for t in still_pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t

            if not done:
                # Timed out → emit keep-alive and loop.
                yield ": keepalive\n\n"
                continue

            for task in done:
                src = pending[task]
                try:
                    raw_entry = task.result()
                except Exception:
                    continue
                if src == "hal0":
                    entry = _hal0_event_to_entry(raw_entry)
                    if entry.id <= last_hal0_id:
                        continue
                    last_hal0_id = entry.id
                else:
                    entry = _lemond_entry_to_entry(raw_entry)
                    if entry.id <= last_lemond_id:
                        continue
                    last_lemond_id = entry.id
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
    """SSE live tail of the unified journal.

    Replays the last ~50 filtered entries synchronously, then streams
    live additions until the client disconnects. Keep-alive comment
    frames pulse every 15s so proxies don't reap idle connections.
    """

    async def _safe() -> Any:
        try:
            async for chunk in _stream_iter(
                request,
                source=source,
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
