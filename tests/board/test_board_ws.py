"""board_ws.py — local card_event streamer for /api/board/events.

hal0 owns the board: the events WS tails the local BoardStore event log instead
of proxying to Hermes. Asserts the frozen frame shape
``{"events":[...], "cursor": N}``, ``since=`` resume/replay, and ``board=``
filtering.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_ws.py -q
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware.error_codes import install as install_errors
from hal0.api.routes import board
from hal0.api.routes import board_ws as board_ws_mod
from hal0.board.store import BoardStore


def _app_with_store(store: BoardStore) -> FastAPI:
    app = FastAPI()
    install_errors(app)
    app.include_router(board.router, prefix="/api/board")
    app.state.board_store = store
    app.state.hermes_kanban = None
    return app


def _store(tmp_path) -> BoardStore:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))
    return s


def test_since_zero_replays_existing_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(board_ws_mod, "POLL_INTERVAL_SECONDS", 0.02)
    store = _store(tmp_path)
    tid = store.create_task({"title": "a", "status": "todo"})["task"]["id"]
    store.update_task(tid, {"status": "ready"})

    app = _app_with_store(store)
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/api/board/events?since=0") as ws:
        frame = ws.receive_json()
    kinds = [e["kind"] for e in frame["events"]]
    assert "created" in kinds and "updated" in kinds
    assert frame["cursor"] == max(e["id"] for e in frame["events"])


def test_since_absent_streams_only_new(tmp_path, monkeypatch) -> None:
    """No ?since= ⇒ start at the latest cursor; a mutation AFTER connect streams."""
    monkeypatch.setattr(board_ws_mod, "POLL_INTERVAL_SECONDS", 0.02)
    store = _store(tmp_path)
    store.create_task({"title": "old"})  # pre-existing, must NOT replay

    app = _app_with_store(store)
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/api/board/events") as ws:
        # New event after the socket is live.
        store.create_task({"title": "new"})
        frame = ws.receive_json()
    kinds = [e["kind"] for e in frame["events"]]
    assert kinds == ["created"]  # only the post-connect create, not the old one


def test_board_filter(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(board_ws_mod, "POLL_INTERVAL_SECONDS", 0.02)
    store = _store(tmp_path)
    store.create_board({"slug": "other"})
    store.create_task({"title": "a"})  # default board
    store.create_task({"title": "b", "board": "other"})

    app = _app_with_store(store)
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/api/board/events?since=0&board=other") as ws:
        frame = ws.receive_json()
    assert all(e["board"] == "other" for e in frame["events"])


def test_frame_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(board_ws_mod, "POLL_INTERVAL_SECONDS", 0.02)
    store = _store(tmp_path)
    store.create_task({"title": "a"})
    app = _app_with_store(store)
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/api/board/events?since=0") as ws:
        frame = ws.receive_json()
    assert set(frame.keys()) == {"events", "cursor"}
    ev = frame["events"][0]
    assert set(ev.keys()) >= {"id", "kind", "task_id", "board", "at"}


def test_close_is_clean_when_no_store(tmp_path) -> None:
    """No store on app.state and no way to build one under an isolated path:
    the bridge closes the socket rather than crashing the server."""
    import hal0.config.paths as paths_mod

    app = FastAPI()
    install_errors(app)
    app.include_router(board.router, prefix="/api/board")
    app.state.hermes_kanban = None
    orig = paths_mod.db_path
    paths_mod.db_path = lambda: tmp_path / "hal0.db"  # type: ignore[assignment]
    try:
        client = TestClient(app, raise_server_exceptions=False)
        with (
            contextlib.suppress(Exception),
            client.websocket_connect("/api/board/events?since=0") as ws,
        ):
            # Lazily built empty store: connects fine, just no events yet.
            ws.close()
    finally:
        paths_mod.db_path = orig  # type: ignore[assignment]
