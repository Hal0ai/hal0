"""Durable activity / audit surface — mounted under ``/api/activity``.

Reads the SQLite :class:`hal0.activity.AuditStore` (``app.state.audit``), the
source of truth for config-mutating actions and system state changes. Unlike
``/api/events`` (volatile ring), this surface survives restarts and carries
before/after state + a success/failure outcome per action.

Read-only; no auth dependency — the slots-page ActivityLog must render during
first-run before any credential exists (same rationale as ``/api/events``).

Endpoints::

    GET /api/activity?since=&category=&action=&severity=&outcome=&actor=
                     &kind=&search=&limit=
        → {"records": [...], "next_since": int, "epoch": str}

    GET /api/activity/stream?since=&<same filters>
        → SSE: durable backfill then live tail (filters honored server-side).

    GET /api/activity/export?fmt=csv|json&<same filters>
        → file download (Content-Disposition: attachment).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse

from hal0.api.middleware.error_codes import Hal0Error

router = APIRouter()

_LIMIT_MAX = 1000
_KEEPALIVE_S = 15.0

_VALID_SEVERITY = {"info", "warn", "error", "ok"}
_VALID_KIND = {"action", "event"}
_VALID_OUTCOME = {"ok", "error", "pending"}
_JSON_COLS = ("before", "after")


class ActivityUnavailable(Hal0Error):
    """The audit store was not initialised on app.state (odd entrypoint)."""

    code = "activity.unavailable"
    status = 503


class ActivityInvalidQuery(Hal0Error):
    """Caller supplied an unsupported filter value (e.g. unknown severity)."""

    code = "activity.invalid_query"
    status = 400


def _store(request: Request):
    store = getattr(request.app.state, "audit", None)
    if store is None:
        raise ActivityUnavailable("activity store unavailable")
    return store


def _epoch(request: Request) -> str:
    return getattr(request.app.state, "audit_epoch", "")


def _validate(severity: str | None, kind: str | None, outcome: str | None) -> None:
    if severity and severity not in _VALID_SEVERITY:
        raise ActivityInvalidQuery(f"unknown severity {severity!r}")
    if kind and kind not in _VALID_KIND:
        raise ActivityInvalidQuery(f"unknown kind {kind!r}")
    if outcome and outcome not in _VALID_OUTCOME:
        raise ActivityInvalidQuery(f"unknown outcome {outcome!r}")


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    # Parse the JSON blobs back into objects so the UI doesn't double-decode.
    for col in _JSON_COLS:
        if d.get(col):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                d[col] = json.loads(d[col])
    return d


@router.get("")
@router.get("/")
async def list_activity(
    request: Request,
    since: int = Query(0, ge=0),
    category: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    outcome: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    limit: int = Query(200, ge=1, le=_LIMIT_MAX),
) -> dict[str, Any]:
    _validate(severity, kind, outcome)
    store = _store(request)
    rows = store.query(
        since=since,
        category=category,
        action=action,
        severity=severity,
        outcome=outcome,
        actor=actor,
        kind=kind,
        search=search,
        limit=limit,
    )
    records = [_row_to_dict(r) for r in rows]
    # Rows come back newest-first; the highest id is the cursor to poll next.
    next_since = records[0]["id"] if records else since
    return {"records": records, "next_since": next_since, "epoch": _epoch(request)}


@router.get("/export")
async def export_activity(
    request: Request,
    fmt: str = Query("json"),
    category: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    outcome: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    search: str | None = None,
) -> Response:
    if fmt not in ("csv", "json"):
        raise ActivityInvalidQuery(f"unsupported export fmt {fmt!r}")
    _validate(severity, kind, outcome)
    store = _store(request)
    blob = store.export(
        fmt=fmt,
        category=category,
        action=action,
        severity=severity,
        outcome=outcome,
        actor=actor,
        kind=kind,
        search=search,
    )
    media = "text/csv" if fmt == "csv" else "application/json"
    return Response(
        content=blob,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="hal0-activity.{fmt}"'},
    )


@router.get("/stream")
async def stream_activity(
    request: Request,
    since: int = Query(0, ge=0),
    category: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    outcome: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    search: str | None = None,
) -> StreamingResponse:
    _validate(severity, kind, outcome)
    store = _store(request)
    epoch = _epoch(request)

    filters = dict(
        category=category,
        action=action,
        severity=severity,
        outcome=outcome,
        actor=actor,
        kind=kind,
        search=search,
    )

    async def gen():
        # Durable backfill first (id > since), oldest→newest for replay.
        backfill = list(
            reversed(
                [_row_to_dict(r) for r in store.query(since=since, limit=_LIMIT_MAX, **filters)]
            )
        )
        cursor = since
        for rec in backfill:
            cursor = max(cursor, rec["id"])
            yield f"data: {json.dumps({'record': rec, 'epoch': epoch})}\n\n"
        # Live tail: poll the store (the audit sink writes async, so we
        # re-query rather than subscribe to the bus — keeps one source of
        # truth and applies the same filters).
        while True:
            if await request.is_disconnected():
                break
            new = list(
                reversed(
                    [
                        _row_to_dict(r)
                        for r in store.query(since=cursor, limit=_LIMIT_MAX, **filters)
                    ]
                )
            )
            if new:
                for rec in new:
                    cursor = max(cursor, rec["id"])
                    yield f"data: {json.dumps({'record': rec, 'epoch': epoch})}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(_KEEPALIVE_S if not new else 1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
