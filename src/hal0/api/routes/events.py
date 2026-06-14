"""Dashboard footer event surface — backfill + live SSE stream.

Mounted under ``/api/events`` by ``hal0.api.create_app``. Reads only; no
auth dependency because the footer must surface state during first-run
before any credential exists. Writers live on ``app.state.events`` and
are emitted from the slot state machine, pull jobs, and lifecycle hooks.

Endpoint contract::

    GET /api/events?since=<id>&type=<glob>&severity=<info|warn|error>&limit=<n=200>
        → {"events": [...], "next_since": int}

    GET /api/events/stream?since=<id>
        → SSE: backfill (id > since) then live tail; one ``data: <json>\\n\\n``
          frame per event. JSON carries its own ``type`` field so we don't
          emit a separate SSE ``event:`` name.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import Hal0Error
from hal0.events import _SEVERITY_ORDER, EventBus

router = APIRouter()

# Hard ceiling so a hostile ``?limit=`` cannot make us materialise the
# whole ring repeatedly. 1000 is well above the 500-deep ring; the API
# still clamps via min(limit, ring_size).
_LIMIT_MAX = 1000

# SSE keep-alive interval. The frontend drops the connection after ~30s
# of silence on some proxies; pulse a comment frame more often than that.
_KEEPALIVE_S: float = 15.0


class EventsUnavailable(Hal0Error):
    """The event bus has not been initialised on app.state.

    Raised when a test or odd entrypoint reaches this router before the
    lifespan ran. The normal app boot path always sets app.state.events.
    """

    code = "events.unavailable"
    status = 503


class EventsInvalidQuery(Hal0Error):
    """Caller supplied an unsupported query-param value (e.g. unknown severity)."""

    code = "events.invalid_query"
    status = 400


def _bus(request: Request) -> EventBus:
    bus: EventBus | None = getattr(request.app.state, "events", None)
    if bus is None:
        raise EventsUnavailable("event bus not initialised on app.state")
    return bus


def _normalise_severity(value: str | None) -> str | None:
    """Reject unknown severities up-front so callers see a 400 not a silent skip."""
    if value is None or value == "":
        return None
    if value not in {"info", "warn", "error"}:
        raise EventsInvalidQuery(
            f"unknown severity {value!r}; expected one of info|warn|error",
            details={"received": value},
        )
    return value


@router.get("")
async def list_events(
    request: Request,
    since: int | None = Query(default=None, ge=0),
    type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=_LIMIT_MAX),
) -> dict[str, Any]:
    """Return a page of events with a cursor for the next call.

    ``next_since`` is the largest id in the returned page (or the supplied
    ``since`` when the page is empty) — clients pass it back as ``since``
    on the next request to receive only deltas.
    """
    bus = _bus(request)
    sev = _normalise_severity(severity)
    events = bus.backfill(since=since, type_glob=type, min_severity=sev, limit=limit)
    next_since = events[-1]["id"] if events else (since or 0)
    # Per-process epoch: event ids restart at 1 on every boot, so a client
    # holding a stale ``since`` would skip all post-restart events until the
    # counter climbed past it. Surfacing the epoch lets the client detect the
    # reset and rewind its cursor to 0 (fixes the footer-blank-after-restart
    # bug). The audit epoch doubles as the process epoch.
    epoch = getattr(request.app.state, "audit_epoch", "")
    return {"events": events, "next_since": next_since, "epoch": epoch}


@router.get("/stream")
async def stream_events(
    request: Request,
    since: int | None = Query(default=None, ge=0),
    type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
) -> StreamingResponse:
    """Server-Sent Events stream: backfill then live tail.

    ``type`` (fnmatch glob, e.g. ``slot.*``) and ``severity`` (min level,
    inclusive) narrow BOTH the backfill and the live tail server-side, so a
    footer wanting only ``slot.*`` no longer receives the full pull-progress
    firehose (which could overflow the subscriber queue and drop frames).

    The replay window is bounded by the ring (500 entries). Clients that
    have been disconnected longer than that lose anything older than the
    oldest ring entry — they're expected to bootstrap fresh from
    ``/api/health`` + ``/api/slots`` after a long outage.
    """
    bus = _bus(request)
    sev = _normalise_severity(severity)
    min_rank = _SEVERITY_ORDER.get(sev, -1) if sev else -1

    def _passes(ev: dict[str, Any]) -> bool:
        if type and not fnmatch.fnmatchcase(ev.get("type", ""), type):
            return False
        return not (min_rank >= 0 and _SEVERITY_ORDER.get(ev.get("severity", "info"), 0) < min_rank)

    async def _gen() -> Any:
        # 1. Subscribe FIRST so events emitted between backfill snapshot
        #    and live tail get queued instead of dropped.
        async with bus.subscribe() as q:
            # 2. Snapshot whatever is already in the ring with id > since,
            #    narrowed by the same type/severity filters as the live tail.
            replay = bus.backfill(since=since, type_glob=type, min_severity=sev, limit=_LIMIT_MAX)
            last_id = since or 0
            for ev in replay:
                yield f"data: {json.dumps(ev)}\n\n"
                last_id = max(last_id, ev["id"])

            # 3. Live tail. Drop any queue entries that overlap the
            #    backfill window so reconnecting clients don't see a
            #    duplicate frame.
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_S)
                except TimeoutError:
                    # Comment frame keeps proxies (caddy default) from
                    # closing the idle connection. Browsers ignore lines
                    # starting with ":" per the SSE spec.
                    yield ": keepalive\n\n"
                    continue
                if ev["id"] <= last_id:
                    continue
                last_id = ev["id"]
                if not _passes(ev):
                    continue
                yield f"data: {json.dumps(ev)}\n\n"

    async def _safe_gen() -> Any:
        try:
            async for chunk in _gen():
                yield chunk
        except asyncio.CancelledError:
            # Client disconnect — let the generator close cleanly. The
            # ``async with subscribe()`` block in _gen unregisters the
            # subscriber on its way out via contextlib.
            with contextlib.suppress(Exception):
                pass
            raise

    return StreamingResponse(
        _safe_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
