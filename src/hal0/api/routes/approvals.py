"""Approval inbox REST routes (mounted under ``/api/agent/approvals``).

The hal0 admin MCP server (ADR-0004 §4) puts every gated tool call into
an in-process :class:`hal0.mcp.approval_queue.ApprovalQueue`. The
dashboard's bell + inbox modal (ADR-0004 §5) reads that queue through
these routes, and the owner approves / denies inline.

Endpoints
---------

::

    GET    /api/agent/approvals            — list_pending
    POST   /api/agent/approvals/{id}/approve  — approve + execute
    POST   /api/agent/approvals/{id}/deny     — deny (no exec)
    GET    /api/agent/approvals/events     — SSE; backfill+live tail

The SSE stream replays the current pending set on subscribe (so a tab
reopened mid-flight sees the same inbox the dashboard does) then emits
``enqueued / approved / denied / executed / failed`` frames as the
queue mutates.

Auth
----

Auth was removed in ADR-0012 — all routes here are open on the local
network. This module declares no auth dependency itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import Hal0Error
from hal0.mcp.approval_queue import ApprovalQueue

router = APIRouter()

# Auth was removed in ADR-0012. POST /approve and POST /deny are
# unrestricted on the local network; access control is LAN-only.


# SSE keep-alive cadence — matches /api/events to keep the proxy
# config story uniform.
_KEEPALIVE_S: float = 15.0


class ApprovalQueueUnavailable(Hal0Error):
    """The ApprovalQueue is not initialised on app.state.

    The orchestrator wires ``app.state.approval_queue`` in the FastAPI
    lifespan. Tests that bypass the lifespan see this 503 — the right
    fix is to populate the state, not to rebuild the routes around it.
    """

    code = "approvals.unavailable"
    status = 503


class ApprovalNotFound(Hal0Error):
    """The requested approval id is not in the queue."""

    code = "approvals.not_found"
    status = 404


class ApprovalAlreadyResolved(Hal0Error):
    """The approval has already been approved/denied."""

    code = "approvals.already_resolved"
    status = 409


def _queue(request: Request) -> ApprovalQueue:
    q: ApprovalQueue | None = getattr(request.app.state, "approval_queue", None)
    if q is None:
        raise ApprovalQueueUnavailable("approval queue not initialised on app.state")
    return q


@router.get("")
async def list_pending(request: Request) -> dict[str, Any]:
    """Return every entry still in the ``pending`` state.

    The dashboard's bell badge reads ``len(approvals)`` off this. The
    inbox modal renders one row per entry with the (tool, args,
    client_id, enqueued_at) tuple so the owner can decide in context.
    """
    queue = _queue(request)
    return {"approvals": queue.list_pending()}


@router.post("/{approval_id}/approve")
async def approve_approval(approval_id: str, request: Request) -> dict[str, Any]:
    """Approve one pending entry; the queue runs the bound executor.

    Returns the entry's final state — ``executed`` on success or
    ``failed`` with the wrapped error message. A 404 covers "id never
    existed"; a 409 covers "id was already resolved" so retry-storm
    behaviour is distinguishable from a stale dashboard tab.
    """
    queue = _queue(request)
    try:
        result = await queue.approve(approval_id)
    except KeyError:
        raise ApprovalNotFound(
            f"approval {approval_id!r} not found",
            details={"approval_id": approval_id},
        ) from None
    except ValueError as exc:
        raise ApprovalAlreadyResolved(
            str(exc),
            details={"approval_id": approval_id},
        ) from None
    return {"approval": result}


@router.post("/{approval_id}/deny")
async def deny_approval(approval_id: str, request: Request) -> dict[str, Any]:
    """Deny one pending entry; no executor runs."""
    queue = _queue(request)
    try:
        result = await queue.deny(approval_id)
    except KeyError:
        raise ApprovalNotFound(
            f"approval {approval_id!r} not found",
            details={"approval_id": approval_id},
        ) from None
    except ValueError as exc:
        raise ApprovalAlreadyResolved(
            str(exc),
            details={"approval_id": approval_id},
        ) from None
    return {"approval": result}


@router.get("/events")
async def approval_events(request: Request) -> StreamingResponse:
    """SSE stream: backfill pending entries then live-tail queue events.

    Pattern mirrors :mod:`hal0.api.routes.events` so the dashboard's
    EventSource handling is identical between the footer event bus and
    the approval inbox. Each frame is ``data: <json>\\n\\n`` where
    ``json`` carries ``kind`` (``enqueued|approved|denied|executed|failed``)
    plus the entry projection.
    """
    queue = _queue(request)

    async def _gen() -> AsyncIterator[str]:
        async with queue.subscribe() as q:
            # Backfill pending state so a fresh subscriber sees the
            # current inbox without polling REST first.
            for entry in queue.list_pending():
                frame = {"kind": "snapshot", "entry": entry}
                yield f"data: {json.dumps(frame)}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                frame = {"kind": event.kind, "entry": event.entry}
                yield f"data: {json.dumps(frame)}\n\n"

    async def _safe_gen() -> AsyncIterator[str]:
        try:
            async for chunk in _gen():
                yield chunk
        except asyncio.CancelledError:
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
