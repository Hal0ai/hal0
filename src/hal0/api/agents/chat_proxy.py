"""Chat WS proxy + session REST shim for the hermes agent runtime.

Bridges the browser to a Hermes ``dashboard`` process bound to loopback
(``127.0.0.1:9119`` per PR-5's systemd ExecStart). The MASTER-PLAN §1
pivot #1 killed the xterm/PTY route — chat is a React composer plus a
JSON-RPC stream over WebSocket. This module is the bridge.

Routes (all mounted at ``/api/agents/{agent_id}/*`` by the API factory):

  ``WS  /events``       — server-sent event mirror from hermes's JSON-RPC bus
  ``WS  /submit``       — bidi JSON-RPC client → hermes (prompt.submit,
                           approval.respond, clarify.respond, ...)
  ``POST /session/create``  — REST shim, proxies hermes ``session.create``
  ``POST /session/resume``  — REST shim, proxies hermes ``session.resume``
  ``GET  /session/history`` — REST shim, proxies hermes ``session.history``

Security baseline (DA-sec-ops MUST-FIX #2 + #3):

* Hermes is loopback-only. The browser cannot talk to it directly.
* WS upgrades are gated by an Origin allowlist + an HMAC session cookie
  (see :mod:`hal0.api.agents._auth`). 403 on any failure.
* Outbound to hermes carries the embed token in ``Authorization:
  Bearer <token>`` — NEVER as a query string. Token comes from
  ``runtime.json`` (chmod 0600). Reload on file change is handled
  best-effort; on 401 from upstream we re-read once and retry.
* uvicorn access log query-string scrub is installed at app start by
  :mod:`hal0.api.middleware.log_scrub`.

Backpressure: ``tool.progress`` events are coalesced server-side at
100ms so a chatty tool doesn't drown the browser. Only ``tool.progress``
is coalesced; ``message.delta`` and every other event pass through
unchanged so token streaming stays smooth.

Reconnect: handled by the BROWSER. The proxy is stateless across WS
connections — the client passes ``session_id`` on connect and we
forward to hermes. Reconnect cadence (jittered 250ms → 4s) is the
client's contract; see ``ui/src/dash/agents/chat/use-hermes-session.js``
in PR-10.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

import httpx
import structlog
import websockets
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, status
from starlette.websockets import WebSocketDisconnect, WebSocketState

from hal0.api.agents._auth import (
    check_ws_origin_and_cookie,
    require_browser_auth,
    set_session_cookie,
)

log = structlog.get_logger(__name__)
router = APIRouter()

# Hermes listens on loopback per PR-5 ExecStart
# ``hermes dashboard --tui --host 127.0.0.1 --port 9119``. Both URLs are
# overridable via env for tests + alt deployments. Read dynamically
# (not as module-level constants) so per-test env overrides take
# effect without a reimport.
DEFAULT_HERMES_HOST: Final[str] = "127.0.0.1"
DEFAULT_HERMES_PORT: Final[int] = 9119

# runtime.json lives where ``hermes_provision``'s install_artifacts phase
# writes it (issue #432 — previously nothing wrote it). The bootstrap
# chmods it 0600; we re-tighten on every read to belt-and-braces against
# permission drift.
RUNTIME_JSON_DEFAULT: Final[str] = "/var/lib/hal0/.hermes/runtime.json"

# tool.progress coalescing window. 100ms matches the rate at which the
# UI rAF batches anyway.
PROGRESS_COALESCE_SECONDS: Final[float] = 0.100

# REST shim timeout for session/* hops. 5s is enough for hermes to
# respond on a healthy loopback; longer than that and we'd rather fail
# fast and let the browser retry.
REST_TIMEOUT_SECONDS: Final[float] = 5.0

# WS keepalive cadence. Lower than the default 20s of the websockets
# library so a proxy in front of hermes (none today, but future
# Traefik termination) doesn't reap an idle connection.
WS_PING_INTERVAL_SECONDS: Final[float] = 20.0


# ---------------------------------------------------------------------------
# Embed-token resolution


def _runtime_json_path() -> Path:
    """Return the on-disk runtime.json path.

    Honours ``HAL0_HERMES_RUNTIME_JSON`` (tests + alt installs). The
    default tracks the bootstrap's install_artifacts writer (#432).
    """
    return Path(os.environ.get("HAL0_HERMES_RUNTIME_JSON", RUNTIME_JSON_DEFAULT))


def _load_embed_token() -> str | None:
    """Read the hermes embed token from runtime.json.

    Returns ``None`` if the file is missing (hermes not provisioned yet)
    so the proxy can degrade gracefully — the WS upgrade will still
    reach hermes, hermes will reject without a token, and we surface
    the resulting close code to the browser unchanged.

    Re-chmods 0600 on every read so a manual ``chmod`` drift heals
    itself. Cheap, idempotent.
    """
    path = _runtime_json_path()
    if not path.exists():
        return None
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hal0.chat_proxy.runtime_json_read_failed", error=str(exc))
        return None
    token = data.get("token") or data.get("embed_token")
    if not isinstance(token, str) or not token:
        return None
    return token


def _hermes_host() -> str:
    return os.environ.get("HAL0_HERMES_HOST", DEFAULT_HERMES_HOST)


def _hermes_port() -> int:
    return int(os.environ.get("HAL0_HERMES_PORT", str(DEFAULT_HERMES_PORT)))


def _hermes_base_url() -> str:
    """HTTP base URL for hermes REST endpoints."""
    return f"http://{_hermes_host()}:{_hermes_port()}"


def _hermes_ws_url(path: str) -> str:
    """WebSocket URL builder for hermes endpoints.

    ``path`` includes the leading slash, e.g. ``/api/events``.
    """
    return f"ws://{_hermes_host()}:{_hermes_port()}{path}"


# ---------------------------------------------------------------------------
# tool.progress coalescer


class ProgressCoalescer:
    """Server-side coalescer for ``tool.progress`` event spam.

    Buffers ``tool.progress`` frames per WebSocket and flushes either
    on a 100ms timer or when the next non-progress event arrives
    (whichever is sooner). Non-progress events pass through unchanged
    and trigger an immediate flush so the user never sees a progress
    frame land *after* the following ``tool.complete``.

    Coalescing keeps the LAST progress frame per ``tool_id`` (the most
    recent preview); intermediate frames are discarded. That matches
    the way the UI renders progress (last write wins anyway).
    """

    def __init__(self, sink: Callable[[str], Awaitable[None]]) -> None:
        self._sink = sink
        # Map of tool_id -> latest raw progress JSON string.
        self._pending: dict[str, str] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._closed = False

    @staticmethod
    def _parse_event_type(raw: str) -> tuple[str | None, str | None]:
        """Inspect the JSON-RPC envelope and return (event_type, tool_id).

        Returns ``(None, None)`` if the frame isn't a JSON-RPC ``event``
        method or the type cannot be determined. We tolerate sloppy
        framings (non-JSON, missing fields) by treating them as
        pass-through.
        """
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return (None, None)
        if not isinstance(obj, dict) or obj.get("method") != "event":
            return (None, None)
        params = obj.get("params")
        if not isinstance(params, dict):
            return (None, None)
        evt_type = params.get("type")
        if not isinstance(evt_type, str):
            return (None, None)
        payload = params.get("payload")
        tool_id = None
        if isinstance(payload, dict):
            tid = payload.get("tool_id") or payload.get("name")
            if isinstance(tid, str):
                tool_id = tid
        return (evt_type, tool_id)

    async def handle(self, raw: str) -> None:
        """Route one upstream frame.

        ``tool.progress`` → buffer + schedule flush.
        anything else → flush any pending progress + forward immediately.
        """
        if self._closed:
            return
        evt_type, tool_id = self._parse_event_type(raw)
        if evt_type == "tool.progress" and tool_id is not None:
            self._pending[tool_id] = raw
            self._schedule_flush()
            return
        # Non-progress event: drain buffered progress first so the
        # ordering invariant (progress before complete) holds.
        await self._flush_now()
        await self._sink(raw)

    def _schedule_flush(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(PROGRESS_COALESCE_SECONDS)
            await self._flush_now()
        except asyncio.CancelledError:  # pragma: no cover — defensive
            raise

    async def _flush_now(self) -> None:
        if not self._pending:
            return
        # Snapshot + clear before sending so a re-entrant handle() doesn't
        # double-deliver. Order is insertion order which matches the
        # original frame ordering.
        frames = list(self._pending.values())
        self._pending.clear()
        for f in frames:
            await self._sink(f)

    async def close(self) -> None:
        """Final flush + cancel any pending timer."""
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
        await self._flush_now()


# ---------------------------------------------------------------------------
# Header helpers


def _outbound_headers(agent_id: str, token: str | None) -> list[tuple[str, str]]:
    """Build the outbound header list for the hop to hermes.

    Always includes ``X-hal0-Agent`` for audit attribution (hermes →
    hal0-admin MCP destructive-action traceback per DA-sec-ops audit
    gap). Includes ``Authorization: Bearer <token>`` only when we have
    a token — missing token means hermes will reject and the browser
    will see the close code. NEVER serialises the token as a query
    string.
    """
    headers: list[tuple[str, str]] = [("X-hal0-Agent", agent_id)]
    if token:
        headers.append(("Authorization", f"Bearer {token}"))
    return headers


# ---------------------------------------------------------------------------
# Bidirectional WS pump


async def _pump(
    source_recv: Callable[[], Awaitable[str]],
    sink_send: Callable[[str], Awaitable[None]],
    *,
    direction: str,
) -> None:
    """Read text frames from ``source_recv`` and write to ``sink_send``.

    Returns cleanly on either side closing. Direction is captured in
    log fields so a sudden disconnect points at the right hop.
    """
    try:
        while True:
            frame = await source_recv()
            await sink_send(frame)
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        log.debug("hal0.chat_proxy.pump_closed", direction=direction)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.chat_proxy.pump_error", direction=direction, error=str(exc))


# ---------------------------------------------------------------------------
# WS proxy plumbing


async def _proxy_ws(
    browser_ws: WebSocket,
    upstream_path: str,
    agent_id: str,
    *,
    coalesce_progress: bool,
) -> None:
    """Bridge a browser WS to a hermes WS at ``upstream_path``.

    ``coalesce_progress`` enables the 100ms ``tool.progress`` coalescer
    on the upstream→browser direction (events stream). The client→
    hermes direction (submit) is uncoalesced.
    """
    token = _load_embed_token()
    headers = _outbound_headers(agent_id, token)
    upstream_url = _hermes_ws_url(upstream_path)

    try:
        connect_kwargs: dict[str, Any] = {
            "additional_headers": headers,
            "ping_interval": WS_PING_INTERVAL_SECONDS,
            "open_timeout": REST_TIMEOUT_SECONDS,
        }
        upstream = await websockets.connect(upstream_url, **connect_kwargs)
    except Exception as exc:
        log.warning(
            "hal0.chat_proxy.upstream_connect_failed",
            agent_id=agent_id,
            upstream=upstream_url,
            error=str(exc),
        )
        # Code 1011 = "internal error" — signals to the browser that
        # the failure is on our end so its retry logic kicks in.
        await browser_ws.close(code=1011)
        return

    # Send-to-browser sink wraps the WS write so the coalescer's
    # callback signature stays Awaitable[None].
    async def to_browser(raw: str) -> None:
        if browser_ws.application_state == WebSocketState.CONNECTED:
            await browser_ws.send_text(raw)

    async def _recv_text() -> str:
        """Receive one frame from upstream as ``str``.

        ``websockets.recv()`` is typed ``str | bytes`` because the
        protocol allows binary frames. Hermes only ever sends text
        (JSON-RPC), but decode defensively so a binary frame doesn't
        crash the pump.
        """
        raw = await upstream.recv()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return raw

    coalescer: ProgressCoalescer | None = None
    if coalesce_progress:
        coalescer = ProgressCoalescer(to_browser)

        async def to_coalescer(raw: str) -> None:
            assert coalescer is not None
            await coalescer.handle(raw)

        upstream_recv = _recv_text
        upstream_to_browser_sink = to_coalescer
    else:
        upstream_recv = _recv_text
        upstream_to_browser_sink = to_browser

    async def from_browser() -> str:
        return await browser_ws.receive_text()

    async def to_upstream(raw: str) -> None:
        await upstream.send(raw)

    try:
        await asyncio.gather(
            _pump(upstream_recv, upstream_to_browser_sink, direction="upstream→browser"),
            _pump(from_browser, to_upstream, direction="browser→upstream"),
        )
    finally:
        if coalescer is not None:
            await coalescer.close()
        with contextlib.suppress(Exception):
            await upstream.close()
        if browser_ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await browser_ws.close()


# ---------------------------------------------------------------------------
# Public WS endpoints


@router.websocket("/{agent_id}/events")
async def events_ws(websocket: WebSocket, agent_id: str) -> None:
    """Server→browser mirror of hermes's JSON-RPC event bus.

    Subscribes to upstream ``/api/events`` and forwards every frame to
    the browser with ``tool.progress`` coalesced at 100ms.
    """
    if not check_ws_origin_and_cookie(websocket):
        # Code 4403 = policy violation. Matches hermes's own convention.
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await _proxy_ws(
        websocket,
        upstream_path="/api/events",
        agent_id=agent_id,
        coalesce_progress=True,
    )


@router.websocket("/{agent_id}/submit")
async def submit_ws(websocket: WebSocket, agent_id: str) -> None:
    """Browser→hermes JSON-RPC submit channel.

    Bidi WS on top of hermes ``/api/ws``. Used by the composer to send
    ``prompt.submit``, ``approval.respond``, ``clarify.respond``, etc.
    Coalescing is OFF here — hermes responds with method results, not
    a progress stream.
    """
    if not check_ws_origin_and_cookie(websocket):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await _proxy_ws(
        websocket,
        upstream_path="/api/ws",
        agent_id=agent_id,
        coalesce_progress=False,
    )


# ---------------------------------------------------------------------------
# REST shim for session/* operations


async def _hermes_rpc(method: str, params: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """Invoke a hermes JSON-RPC method via HTTP.

    Hermes exposes the same dispatch surface that the WS gateway hosts
    via its REST shim at ``/api/<method>``. On a 401 from upstream we
    reload runtime.json once + retry; that handles the boot race where
    hermes rewrites its token shortly after first bind.

    Returns the ``result`` field of the JSON-RPC response. Raises an
    HTTPException with a hermes-shaped error if upstream errors.
    """
    token = _load_embed_token()
    headers = dict(_outbound_headers(agent_id, token))
    headers["Content-Type"] = "application/json"
    url = f"{_hermes_base_url()}/api/{method}"
    envelope = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": int(time.time() * 1000),
    }
    async with httpx.AsyncClient(timeout=REST_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(url, json=envelope, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"hermes_unreachable: {exc}") from exc

        if resp.status_code == 401:
            # Embed-token rotated between read + send. One synchronous
            # reload + one retry, no further loop.
            token = _load_embed_token()
            headers = dict(_outbound_headers(agent_id, token))
            headers["Content-Type"] = "application/json"
            try:
                resp = await client.post(url, json=envelope, headers=headers)
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"hermes_unreachable: {exc}") from exc

        if resp.status_code >= 500:
            raise HTTPException(status_code=502, detail=f"hermes_error: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text[:200])

    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="hermes_bad_response") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="hermes_bad_response")
    if body.get("error"):
        err = body["error"]
        detail = err.get("message") if isinstance(err, dict) else str(err)
        raise HTTPException(status_code=400, detail=detail or "hermes_rpc_error")
    result = body.get("result")
    if not isinstance(result, dict):
        return {"result": result}
    return result


@router.get("/{agent_id}/session/handshake")
async def session_handshake(agent_id: str, response: Response) -> dict[str, Any]:
    """Mint a session cookie + return identity to the browser.

    The dashboard calls this once on first attach. Sets the
    ``hal0_session`` cookie so subsequent WS upgrades pass the
    :func:`_auth.check_ws_origin_and_cookie` gate.

    No upstream hop — the cookie is purely the browser-side seam.
    """
    set_session_cookie(response)
    return {"agent_id": agent_id, "ok": True}


@router.post("/{agent_id}/session/create", status_code=status.HTTP_200_OK)
async def session_create(
    agent_id: str,
    request: Request,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy hermes ``session.create``."""
    require_browser_auth(request)
    params = payload or {}
    return await _hermes_rpc("session.create", params, agent_id)


@router.post("/{agent_id}/session/resume", status_code=status.HTTP_200_OK)
async def session_resume(
    agent_id: str,
    request: Request,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy hermes ``session.resume``."""
    require_browser_auth(request)
    params = payload or {}
    return await _hermes_rpc("session.resume", params, agent_id)


@router.get("/{agent_id}/session/history")
async def session_history(
    agent_id: str,
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """Proxy hermes ``session.history``.

    ``session_id`` comes in as a query string from the browser; the
    embed token NEVER does — that's the seam DA-sec-ops MUST-FIX #3
    locks down. Both this route and the WS upgrade get the token from
    runtime.json + send it as ``Authorization: Bearer``.
    """
    require_browser_auth(request)
    return await _hermes_rpc("session.history", {"session_id": session_id}, agent_id)


__all__ = [
    "DEFAULT_HERMES_HOST",
    "DEFAULT_HERMES_PORT",
    "PROGRESS_COALESCE_SECONDS",
    "REST_TIMEOUT_SECONDS",
    "RUNTIME_JSON_DEFAULT",
    "ProgressCoalescer",
    "router",
]
