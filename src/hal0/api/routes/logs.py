"""Log endpoints (mounted under /api/logs).

Tail and stream journald entries for hal0's systemd units. (The slot
routes do a SEPARATE SSE journalctl tail — see ``slots.py``'s
``/api/slots/{name}/logs/stream``, backed by :mod:`hal0.slots.logs` —
not this module's ``journalctl_sse()``; the two don't currently share
an implementation, despite the similarity.)

Endpoints:
    GET /api/logs?unit=<u>&n=<N>&since=<ts>&level=<lvl>
        Return the last N journal entries for the named unit.
    GET /api/logs/stream?unit=<u>&level=<lvl>&since=<ts>
        SSE tail of the unit's journal output.

Secret redaction (api-logs-redact)
-----------------------------------

Both endpoints proxy raw journald output. Journald lines routinely
carry Bearer tokens, ``HAL0_BEARER_TOKEN=`` env prints, and hal0's own
``*_KEY=``-shaped admin/client credentials in error breadcrumbs — the
same leak shapes the MCP ``logs_tail``/``slot_logs`` tools were hardened
against (security review MED-1). This route streamed those lines with
zero redaction, an independent leak path surfaced by the SEC-mcp-clientid
lane. Every line is now passed through
:func:`hal0.api._redact.redact_log_line` — the same regex the MCP admin
server uses (see :mod:`hal0.mcp.admin`) — before it reaches a client,
whether via the ``lines`` array or the SSE stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from hal0.api._redact import redact_log_line
from hal0.api.middleware.error_codes import Hal0Error

router = APIRouter()


# Levels accepted on ?level= -- mapped to journalctl --priority numbers.
# journalctl uses syslog priorities 0=emerg .. 7=debug, and --priority=N
# means "messages of priority N and lower number (more severe)", so
# warning=4 returns warning + error + critical + alert + emergency.
_LEVELS: dict[str, str] = {
    "emerg": "0",
    "emergency": "0",
    "alert": "1",
    "crit": "2",
    "critical": "2",
    "err": "3",
    "error": "3",
    "warn": "4",
    "warning": "4",
    "notice": "5",
    "info": "6",
    "debug": "7",
}


class LogsError(Hal0Error):
    """Logs endpoint validation/runtime errors."""

    code = "system.logs_error"
    status = 400


def _validate_unit(unit: str) -> str:
    """Validate a systemd unit name.

    Rejects shell-special characters so the unit string can safely be
    passed straight to journalctl. Acceptable forms::

        hal0-api
        hal0-api.service
        hal0-slot@primary
        hal0-slot@primary.service
    """
    import re

    if not unit or not unit.strip():
        raise LogsError(
            "'unit' query parameter is required",
            details={"param": "unit"},
        )
    unit = unit.strip()
    # systemd unit names: letters, digits, '@-_.:' — be conservative.
    if not re.match(r"^[A-Za-z0-9@_\-.:]+$", unit):
        raise LogsError(
            f"invalid unit name {unit!r}",
            details={"unit": unit, "hint": "only alnum + @-_.: are allowed"},
        )
    return unit


def _resolve_level(level: str | None) -> str | None:
    """Map a level alias to a journalctl --priority value."""
    if level is None or not level.strip():
        return None
    key = level.strip().lower()
    if key not in _LEVELS:
        raise LogsError(
            f"invalid level {level!r}",
            details={
                "level": level,
                "allowed": sorted(set(_LEVELS.keys())),
            },
        )
    return _LEVELS[key]


# ── Shared SSE helper ──────────────────────────────────────────────────────


async def journalctl_sse(
    unit: str,
    *,
    level: str | None = None,
    since: str | None = None,
) -> Any:
    """Async generator that yields SSE frames tailing ``journalctl -f -u <unit>``.

    Each non-empty line becomes a ``data: <json-string>`` SSE event so
    clients can ``JSON.parse`` the payload without de-quoting. Gracefully
    exits with a single ``event: error`` frame when journalctl is missing
    (CI hosts without systemd, mac dev boxes).

    Each line is redacted via :func:`hal0.api._redact.redact_log_line`
    before it's framed. NOTE: despite this module's file-level docstring,
    the slot-scoped ``/api/slots/{name}/logs/stream`` route does NOT call
    this generator — it has its own independent journalctl plumbing in
    :mod:`hal0.slots.logs` (``tail_journal``/``read_tail``). That stream
    route now applies the same :func:`hal0.api._redact.redact_log_line`
    helper itself, at its own SSE-framing point in ``slots.py``
    (lane/slotlogs-redact) — so both routes share the redaction
    behaviour even though they don't share this generator.
    """
    if shutil.which("journalctl") is None:
        yield 'event: error\ndata: {"message":"journalctl unavailable"}\n\n'
        return

    cmd = [
        "journalctl",
        "-u",
        unit,
        "-f",
        "-n",
        "0",
        "--output=cat",
        "--no-pager",
    ]
    if level is not None:
        cmd.extend(["--priority", level])
    if since:
        cmd.extend(["--since", since])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            yield f"data: {json.dumps(redact_log_line(line))}\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(ProcessLookupError, OSError):
            await proc.wait()


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("")
async def list_logs(
    unit: str = Query(..., description="systemd unit name, e.g. 'hal0-api'"),
    n: int = Query(200, ge=1, le=5000, description="number of trailing lines"),
    since: str | None = Query(
        None,
        description="journalctl --since value (ISO timestamp or '5min ago')",
    ),
    level: str | None = Query(None, description="filter to this priority and higher"),
) -> dict[str, Any]:
    """Return the last ``n`` journal entries for ``unit``.

    Best-effort: on hosts without journalctl returns
    ``{"unit": ..., "lines": [], "hint": ...}`` so the UI can render
    "No logs available" instead of treating it as an error.
    """
    unit = _validate_unit(unit)
    priority = _resolve_level(level)

    if shutil.which("journalctl") is None:
        return {
            "unit": unit,
            "lines": [],
            "count": 0,
            "hint": "journalctl not available on this host",
        }

    cmd = [
        "journalctl",
        "-u",
        unit,
        "-n",
        str(n),
        "--no-pager",
        "-o",
        "short-iso",
    ]
    if priority is not None:
        cmd.extend(["--priority", priority])
    if since:
        cmd.extend(["--since", since])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        return {
            "unit": unit,
            "lines": [],
            "count": 0,
            "hint": "journalctl timed out",
        }

    text = stdout.decode("utf-8", errors="replace")
    lines = [redact_log_line(ln) for ln in text.splitlines() if ln]
    return {
        "unit": unit,
        "lines": lines,
        "count": len(lines),
    }


@router.get("/stream")
async def stream_logs(
    unit: str = Query(..., description="systemd unit name, e.g. 'hal0-api'"),
    level: str | None = Query(None, description="filter to this priority and higher"),
    since: str | None = Query(None, description="journalctl --since value"),
) -> StreamingResponse:
    """SSE tail of ``unit``'s journald output, line-by-line.

    Closes its subprocess on client disconnect. Gracefully degrades with
    a single ``event: error`` frame when journalctl isn't on PATH.
    """
    unit = _validate_unit(unit)
    priority = _resolve_level(level)

    return StreamingResponse(
        journalctl_sse(unit, level=priority, since=since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
