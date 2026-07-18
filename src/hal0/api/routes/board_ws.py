"""Operator Board events-WS — streams the hal0-local ``card_event`` feed.

hal0 owns the board, so the ``/api/board/events`` WS no longer proxies to the
Hermes kanban ``/events`` upstream. It polls the local
:class:`hal0.board.store.BoardStore` event log and pushes the SAME frozen frame
shape the proxy relayed:

    {"events": [ {id, kind, task_id, board, at, json}, ... ], "cursor": N}

Every board mutation (operator's REST write, the agent chat's tool call, a
future worker's writeback) appends a ``card_event`` row, so this one transport
reflects them all — the frozen "board reflects ALL mutations through the
/events WS" rule (ui/CONTRACTS.md), now sourced from hal0's own store.

Wire contract preserved:

* the browser passes ``since`` (last cursor) and ``board`` (pin) as query
  params; ``since`` absent ⇒ stream only NEW events from now (no history dump),
  ``since=<n>`` ⇒ resume/replay from that cursor.
* each frame carries the max ``cursor`` seen, which the browser echoes back as
  ``?since=`` on reconnect.

No token, no upstream, no loopback dependency — the events are local.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import structlog
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

log = structlog.get_logger(__name__)

#: How often the poller checks the store for new events (Hermes polled its
#: task_events table at 300ms; matched here for parity).
POLL_INTERVAL_SECONDS = 0.3

#: Max events per frame (bounds a burst after a long disconnect).
_FRAME_LIMIT = 200


async def _ensure_store(browser_ws: WebSocket):
    """Return the app-state BoardStore, building + first-boot-initialising it on
    first use (mirrors the REST route's ``_store`` helper) so the WS works even
    when it is the first board surface touched this process."""
    from hal0.board.store import BoardStore

    app = browser_ws.scope.get("app")
    state = getattr(app, "state", None)
    if state is None:
        return None
    store = getattr(state, "board_store", None)
    if store is None:
        store = BoardStore()
        state.board_store = store
    if not store.initialized:
        client = getattr(state, "hermes_kanban", None)
        await store.ensure_initialized(client)
    return store


def _start_cursor(browser_ws: WebSocket, store) -> int:
    """Resolve the initial cursor from ``?since=``.

    Absent/blank ⇒ start at the latest cursor (only NEW events). A parseable
    integer (including 0) ⇒ start there so the client can replay a gap.
    """
    raw = browser_ws.query_params.get("since")
    if raw is None or raw == "":
        return store.latest_cursor()
    try:
        return int(raw)
    except ValueError:
        return store.latest_cursor()


async def proxy_board_events(browser_ws: WebSocket) -> None:
    """Stream local board events to an already-accepted browser WS.

    Named ``proxy_board_events`` for call-site stability (the route imports it),
    though it no longer proxies — it tails the local store. Runs a poll loop
    alongside a browser-drain task; either side closing ends the bridge.
    """
    store = await _ensure_store(browser_ws)
    if store is None:
        log.warning("hal0.board_ws.no_store")
        if browser_ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await browser_ws.close(code=1011)
        return

    board = browser_ws.query_params.get("board")
    cursor = _start_cursor(browser_ws, store)
    stop = asyncio.Event()

    async def drain_browser() -> None:
        """Consume inbound frames purely to notice the browser disconnecting."""
        try:
            while True:
                await browser_ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            stop.set()

    async def poll() -> None:
        nonlocal cursor
        try:
            while not stop.is_set():
                events, new_cursor = store.events_since(cursor, board=board, limit=_FRAME_LIMIT)
                if events:
                    cursor = new_cursor
                    if browser_ws.application_state != WebSocketState.CONNECTED:
                        break
                    await browser_ws.send_text(json.dumps({"events": events, "cursor": cursor}))
                # Sleep one tick, but wake early if the browser drain signals stop.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("hal0.board_ws.poll_error", error=str(exc))
        finally:
            stop.set()

    drain = asyncio.create_task(drain_browser())
    try:
        await poll()
    finally:
        drain.cancel()
        with contextlib.suppress(Exception):
            await drain
        if browser_ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await browser_ws.close()


__all__ = ["POLL_INTERVAL_SECONDS", "proxy_board_events"]
