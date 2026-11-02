"""Tests for board_ws.py — the /api/board/events proxy to Hermes.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_ws.py -q
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

import hal0.api.routes.board_ws as board_ws_mod
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board import BoardNoToken, HermesKanbanClient
from hal0.board.client import BoardClient


class _FakeUpstream:
    """Yields ``frames`` then ends per ``end``: close | stop | error | hold.

    ``hold`` blocks until a frame was sent from the browser (or released).
    """

    def __init__(self, frames: list, end: str = "close") -> None:
        self._frames = list(frames)
        self._end = end
        self.sent: list = []
        self._got = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            item = self._frames.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self._end == "stop":
            raise StopAsyncIteration
        if self._end == "error":
            raise ConnectionClosedError(None, None)
        if self._end == "hold":
            await asyncio.wait_for(self._got.wait(), 5)
        raise ConnectionClosedOK(None, None)

    async def send(self, data) -> None:
        self.sent.append(data)
        self._got.set()


class _FakeConnect:
    def __init__(self, upstream: _FakeUpstream | None, urls: list[str], exc=None) -> None:
        self._upstream = upstream
        self._urls = urls
        self._exc = exc
        self.kwargs: dict = {}
        self.exited = False

    def __call__(self, url: str, **kw):
        self._urls.append(url)
        self.kwargs = kw
        return self

    async def __aenter__(self):
        if self._exc:
            raise self._exc
        return self._upstream

    async def __aexit__(self, *a):
        self.exited = True
        return None


@pytest.fixture
def app_client(monkeypatch):
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url="http://hermes.test:9119",
    )
    kanban = HermesKanbanClient(
        base_url="http://hermes.test:9119", token_resolver=lambda: "tok", http_client=http
    )
    app.state.hermes_kanban = kanban
    app.state.board_client = BoardClient(kanban)
    app.state.board_installed = True
    monkeypatch.setattr(board_ws_mod, "read_dashboard_token", lambda: "SECRET-TOK")
    with TestClient(app) as c:
        yield app, c


def test_ws_appends_token_and_passthrough_query(app_client, monkeypatch) -> None:
    _, client = app_client
    urls: list[str] = []
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(_FakeUpstream([]), urls))
    with (
        client.websocket_connect(
            "/api/board/events?since=5&board=alpha&tenant=x&evil=1&token=BROWSER"
        ) as ws,
        pytest.raises(WebSocketDisconnect),
    ):
        ws.receive_text()
    parsed = urlsplit(urls[0])
    assert (parsed.scheme, parsed.netloc) == ("ws", "hermes.test:9119")
    assert parsed.path == "/api/plugins/kanban/events"
    assert parse_qs(parsed.query) == {
        "since": ["5"],
        "board": ["alpha"],
        "tenant": ["x"],
        "token": ["SECRET-TOK"],
    }


def test_ws_pumps_frames_both_ways(app_client, monkeypatch) -> None:
    _, client = app_client
    up = _FakeUpstream(['{"cursor":1}', '{"cursor":2}'], end="hold")
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(up, []))
    with client.websocket_connect("/api/board/events") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == '{"cursor":1}'
        assert ws.receive_text() == '{"cursor":2}'
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
    assert ei.value.code == 1000
    assert up.sent == ["ping"]


def test_ws_upstream_failure_closes_1011(app_client, monkeypatch) -> None:
    _, client = app_client
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(None, [], exc=OSError("refused")))
    with (
        client.websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        ws.receive_text()
    assert ei.value.code == 1011


def test_ws_no_token_closes_1011(app_client, monkeypatch) -> None:
    _, client = app_client

    def _boom() -> str:
        raise BoardNoToken("missing")

    monkeypatch.setattr(board_ws_mod, "read_dashboard_token", _boom)
    urls: list[str] = []
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(_FakeUpstream([]), urls))
    with (
        client.websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        ws.receive_text()
    assert ei.value.code == 1011
    assert urls == []


def test_ws_not_installed_closes_1011(app_client, monkeypatch) -> None:
    app, client = app_client
    app.state.board_client = None
    app.state.board_installed = False
    urls: list[str] = []
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(_FakeUpstream([]), urls))
    with (
        client.websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        ws.receive_text()
    assert ei.value.code == 1011
    assert urls == []


def _closes_with(client, monkeypatch, fake) -> int:
    monkeypatch.setattr(board_ws_mod, "connect", fake)
    with (
        client.websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        ws.receive_text()
    return ei.value.code


def test_ws_connect_bypasses_proxy_env(app_client, monkeypatch) -> None:
    fake = _FakeConnect(_FakeUpstream([]), [])
    _closes_with(app_client[1], monkeypatch, fake)
    assert fake.kwargs["proxy"] is None


def test_ws_empty_params_not_forwarded(app_client, monkeypatch) -> None:
    urls: list[str] = []
    _closes_with(app_client[1], monkeypatch, _FakeConnect(_FakeUpstream([]), urls))  # baseline
    urls.clear()
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(_FakeUpstream([]), urls))
    with (
        app_client[1].websocket_connect("/api/board/events?since=&board=alpha") as ws,
        pytest.raises(WebSocketDisconnect),
    ):
        ws.receive_text()
    q = parse_qs(urlsplit(urls[0]).query)
    assert q["board"] == ["alpha"]
    assert "since" not in q


def test_ws_stop_async_iteration_closes_1000(app_client, monkeypatch) -> None:
    fake = _FakeConnect(_FakeUpstream([], end="stop"), [])
    assert _closes_with(app_client[1], monkeypatch, fake) == 1000


def test_ws_midstream_closed_error_closes_1011(app_client, monkeypatch) -> None:
    fake = _FakeConnect(_FakeUpstream(["a"], end="error"), [])
    monkeypatch.setattr(board_ws_mod, "connect", fake)
    with (
        app_client[1].websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        assert ws.receive_text() == "a"
        ws.receive_text()
    assert ei.value.code == 1011


def test_ws_upstream_close_code_forwarded(app_client, monkeypatch) -> None:
    err = ConnectionClosedError(Close(1008, "stale board"), None)
    fake = _FakeConnect(_FakeUpstream(["a", err]), [])
    monkeypatch.setattr(board_ws_mod, "connect", fake)
    with (
        app_client[1].websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect) as ei,
    ):
        assert ws.receive_text() == "a"
        ws.receive_text()
    assert ei.value.code == 1008


def test_ws_unexpected_pump_exception_closes_1011(app_client, monkeypatch) -> None:
    fake = _FakeConnect(_FakeUpstream([ValueError("boom")]), [])
    assert _closes_with(app_client[1], monkeypatch, fake) == 1011


def test_ws_binary_frame_forwarded(app_client, monkeypatch) -> None:
    up = _FakeUpstream([], end="hold")
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(up, []))
    with (
        app_client[1].websocket_connect("/api/board/events") as ws,
        pytest.raises(WebSocketDisconnect),
    ):
        ws.send_bytes(b"\x00\x01")
        ws.receive_text()
    assert up.sent == [b"\x00\x01"]


def test_ws_browser_disconnect_closes_upstream(app_client, monkeypatch) -> None:
    fake = _FakeConnect(_FakeUpstream([], end="hold"), [])
    monkeypatch.setattr(board_ws_mod, "connect", fake)
    with app_client[1].websocket_connect("/api/board/events") as ws:
        ws.close()
    # TestClient joins the app task on exit, so the handler has finished.
    assert fake.exited


def test_ws_https_base_becomes_wss(app_client, monkeypatch) -> None:
    app, client = app_client
    app.state.hermes_kanban = HermesKanbanClient(
        base_url="https://hermes.test", token_resolver=lambda: "t"
    )
    urls: list[str] = []
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(_FakeUpstream([]), urls))
    with client.websocket_connect("/api/board/events") as ws, pytest.raises(WebSocketDisconnect):
        ws.receive_text()
    assert urls[0].startswith("wss://hermes.test/api/plugins/kanban/events")
