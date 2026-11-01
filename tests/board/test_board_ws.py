"""Tests for board_ws.py — the /api/board/events proxy to Hermes.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_ws.py -q
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK

import hal0.api.routes.board_ws as board_ws_mod
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board import BoardNoToken, HermesKanbanClient
from hal0.board.client import BoardClient


class _FakeUpstream:
    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        raise ConnectionClosedOK(None, None)

    async def send(self, data: str) -> None:
        self.sent.append(data)


class _FakeConnect:
    def __init__(self, upstream: _FakeUpstream | None, urls: list[str], exc=None) -> None:
        self._upstream = upstream
        self._urls = urls
        self._exc = exc

    def __call__(self, url: str, **_kw):
        self._urls.append(url)
        return self

    async def __aenter__(self):
        if self._exc:
            raise self._exc
        return self._upstream

    async def __aexit__(self, *a):
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
    up = _FakeUpstream(['{"cursor":1}', '{"cursor":2}'])
    monkeypatch.setattr(board_ws_mod, "connect", _FakeConnect(up, []))
    with client.websocket_connect("/api/board/events") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == '{"cursor":1}'
        assert ws.receive_text() == '{"cursor":2}'
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
    assert ei.value.code == 1000
    assert up.sent in ([], ["ping"])  # browser frame may race the clean close


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
