"""Operator Board events-WS proxy — browser ⇄ Hermes kanban ``/events``.

The Hermes kanban plugin pushes ``{"events": [...], "cursor": N}`` frames on
``/api/plugins/kanban/events``. Browsers cannot set ``Authorization`` on a WS
upgrade, so Hermes takes the dashboard session token as the ``?token=`` query
param. This proxy:

* resolves the board client first, so a box without Hermes closes the browser
  socket before any upstream attempt,
* reads the stable dashboard token from the per-install token file
  (:func:`hal0.board.auth.read_dashboard_token`) — never from the browser,
* threads only ``since`` / ``board`` / ``tenant`` from the browser query,
* bidi-pumps frames and tears both directions down when either side closes.

Every failure to reach the upstream closes the browser socket with ``1011`` so
the client's reconnect logic engages. The token is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlencode, urlsplit

import structlog
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, WebSocketException

from hal0.board import (
    DEFAULT_BASE_URL,
    KANBAN_BASE_PATH,
    BoardNotInstalled,
    BoardNoToken,
    HermesKanbanClient,
)
from hal0.board.auth import read_dashboard_token
from hal0.board.client import resolve_board_client

log = structlog.get_logger(__name__)

# Query params the browser may thread upstream. The token is NOT in this set.
_PASSTHROUGH_QUERY = ("since", "board", "tenant")

WS_PING_INTERVAL_SECONDS = 20.0
WS_OPEN_TIMEOUT_SECONDS = 5.0


def _http_to_ws(base_url: str) -> str:
    """Map an http(s) base URL to its ws(s) equivalent."""
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}"


def _upstream_base_url(browser_ws: WebSocket) -> str:
    kanban = getattr(browser_ws.app.state, "hermes_kanban", None)
    if isinstance(kanban, HermesKanbanClient):
        return kanban.base_url
    return DEFAULT_BASE_URL


def _build_upstream_url(browser_ws: WebSocket, *, token: str) -> str:
    qs: dict[str, str] = {}
    for key in _PASSTHROUGH_QUERY:
        val = browser_ws.query_params.get(key)
        if val is not None:
            qs[key] = val
    qs["token"] = token
    ws_base = _http_to_ws(_upstream_base_url(browser_ws))
    return f"{ws_base}{KANBAN_BASE_PATH}/events?{urlencode(qs)}"


async def _close_browser(browser_ws: WebSocket, code: int) -> None:
    if browser_ws.application_state == WebSocketState.CONNECTED:
        with contextlib.suppress(Exception):
            await browser_ws.close(code=code)


async def _upstream_to_browser(upstream, browser_ws: WebSocket) -> int:
    """Relay upstream frames to the browser; return the browser close code."""
    try:
        async for frame in upstream:
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8", errors="replace")
            await browser_ws.send_text(frame)
    except ConnectionClosedOK:
        return 1000
    except (WebSocketException, WebSocketDisconnect, RuntimeError):
        return 1011
    return 1000


async def _browser_to_upstream(browser_ws: WebSocket, upstream) -> int:
    try:
        while True:
            await upstream.send(await browser_ws.receive_text())
    except WebSocketDisconnect:
        return 1000
    except (WebSocketException, RuntimeError):
        return 1011


async def proxy_board_events(browser_ws: WebSocket) -> None:
    """Bridge an accepted browser WS to the upstream kanban events WS."""
    path = browser_ws.url.path
    try:
        resolve_board_client(browser_ws.app)
        token = read_dashboard_token()
    except BoardNotInstalled:
        log.warning("board.events_not_installed", path=path)
        await _close_browser(browser_ws, 1011)
        return
    except BoardNoToken:
        log.warning("board.events_no_token", path=path)
        await _close_browser(browser_ws, 1011)
        return

    url = _build_upstream_url(browser_ws, token=token)
    cm = connect(
        url,
        ping_interval=WS_PING_INTERVAL_SECONDS,
        open_timeout=WS_OPEN_TIMEOUT_SECONDS,
    )
    try:
        upstream = await cm.__aenter__()
    except Exception as exc:
        # Never log ``exc``/``url``: either may carry the ?token= query.
        log.warning("board.events_upstream_failed", error=type(exc).__name__)
        await _close_browser(browser_ws, 1011)
        return

    tasks = [
        asyncio.ensure_future(_upstream_to_browser(upstream, browser_ws)),
        asyncio.ensure_future(_browser_to_upstream(browser_ws, upstream)),
    ]
    code = 1000
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        code = next(iter(done)).result()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)
        await _close_browser(browser_ws, code)


__all__ = ["proxy_board_events"]
