"""Lemonade log proxy endpoints (mounted under /api/lemonade).

PR-11 (plan §11 + ADR-0008 §3): proxies the Lemonade ``/logs/stream``
WebSocket to a hal0-api SSE endpoint the dashboard subscribes to. Two
streams are surfaced:

- ``GET /api/lemonade/logs/stream`` — every parsed log entry from
  lemond, forwarded as JSON SSE frames. Consumed by PR-14's journal
  panel (out of scope here; this endpoint just exposes the surface).

- ``GET /api/lemonade/events/stream`` — structured event stream
  emitted from filtered log lines. The first event surface is
  ``nuclear_evict``: when lemond emits the trigger line
  (per :data:`hal0.api.routes.slots.NUCLEAR_EVICT_TRIGGER`), the
  dashboard's banner subscriber sees a JSON payload it can render as
  a transient toast.

Both streams degrade gracefully when lemond is down — clients receive
no events and reconnect on their own cadence. No keep-alive frames are
sent; the underlying ``LemonadeClient.stream_logs`` iterator simply
yields nothing when the daemon is unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

# Reuse the trigger constant from slots.py so the contract stays in one
# place. A future filter (e.g. evict-warning, pull-progress) joins this
# same module without dragging slots.py in.
from hal0.api.routes.slots import NUCLEAR_EVICT_TRIGGER

router = APIRouter()


def _extract_message(entry: dict[str, Any]) -> str:
    """Pick the human-readable log line out of a lemond log frame.

    lemond's protocol (per the ``hal0_lemonade_ws_protocol`` memory):

      ``{"op": "logs.entry", "entry": {"message": "...", "level": ...}}``
      ``{"op": "logs.snapshot", "entries": [{...}, ...]}``

    Both forms route through this helper after the SSE proxy splits
    snapshot batches into individual entries.
    """
    msg = entry.get("message")
    if isinstance(msg, str):
        return msg
    # Some lemond builds nest the line under "text" or "msg"; tolerate
    # any of them so a protocol shift doesn't silently swallow the
    # nuclear-evict trigger.
    for alt in ("text", "msg", "line"):
        v = entry.get(alt)
        if isinstance(v, str):
            return v
    return ""


def _iter_entries(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a lemond log frame into individual entry dicts.

    ``logs.snapshot`` carries a list under ``entries``; ``logs.entry``
    carries a single dict under ``entry``. Returns the entries verbatim
    so callers preserve any provider-specific metadata (level, ts).

    lemond keys these frames on ``type``; we read ``type`` first and fall
    back to ``op`` so a protocol shift doesn't silently drop entries.
    """
    op = frame.get("type") or frame.get("op")
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
    # Unknown ops are surfaced as-is so a protocol bump doesn't drop
    # entries on the floor — the consumer can still inspect ``raw``.
    return [frame]


async def _lemonade_log_entries(request: Request) -> Any:
    """Async iterator yielding flattened log entries from lemond.

    Wraps ``LemonadeClient.stream_logs`` and re-shapes batched
    snapshot frames into per-entry yields. Stops when the WebSocket
    closes; the caller (the SSE proxy) returns an empty event stream
    in that case.
    """
    from hal0.providers import lemonade_provider

    client = lemonade_provider().client()
    async for frame in client.stream_logs():
        for entry in _iter_entries(frame):
            yield entry


@router.get("/logs/stream")
async def lemonade_logs_stream(request: Request) -> StreamingResponse:
    """SSE proxy of lemond's ``/logs/stream`` WebSocket.

    Each parsed log entry is re-emitted as a ``data: <json>`` SSE
    frame. Consumed by PR-14's journal panel (out of scope here). The
    JSON shape is whatever lemond emits — typically
    ``{"message": "...", "level": "info", "ts": "..."}``.
    """

    async def event_source() -> Any:
        try:
            async for entry in _lemonade_log_entries(request):
                yield f"data: {json.dumps(entry)}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/stream")
async def lemonade_events_stream(request: Request) -> StreamingResponse:
    """SSE stream of structured events derived from lemond logs.

    Right now the only event surface is ``nuclear_evict`` — when a
    lemond log entry contains :data:`NUCLEAR_EVICT_TRIGGER`, a frame
    is emitted::

        event: nuclear_evict
        data: {"type": "nuclear_evict", "message": "...", "ts": "..."}

    The dashboard subscribes to this stream and renders a toast banner
    on each frame. Other event types (eviction warnings, pull
    failures) attach here in later PRs.

    Per ADR-0008 §3 nuclear-evict is rare-but-visible — surfacing it
    inline rather than burying in the journal panel matters more than
    the bytes saved by combining the two streams.
    """

    async def event_source() -> Any:
        with contextlib.suppress(asyncio.CancelledError):
            async for entry in _lemonade_log_entries(request):
                msg = _extract_message(entry)
                if not msg:
                    continue
                if NUCLEAR_EVICT_TRIGGER not in msg:
                    continue
                payload = {
                    "type": "nuclear_evict",
                    "message": msg,
                    "ts": entry.get("ts") or time.time(),
                }
                yield f"event: nuclear_evict\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "NUCLEAR_EVICT_TRIGGER",
    "_extract_message",
    "_iter_entries",
    "router",
]
