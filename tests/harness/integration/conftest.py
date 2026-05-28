"""Shared fixtures for the v0.3 chat + persona δ-harness integration tests.

Reuses the FakeHermes uvicorn pattern from ``tests/api/test_chat_proxy.py``
but elevates it to harness scope (one fake-hermes per test, the same
client shape PR-9 / PR-10's production code talks to).

Lives in ``tests/harness/integration/`` so the δ-tier suite can be
filtered out of the normal ``pytest tests/`` collection if needed —
``pytest tests/harness/integration/`` runs only these. They DO get
picked up by ``pytest tests/`` though, because the master plan §4
PR-11 requires them in the default test pass.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class FakeWsServer:
    """Minimal hermes replacement for δ-harness chat round-trip tests.

    Tracks every inbound WS frame on ``/api/ws`` + every push on
    ``/api/events``. Tests dispatch outbound frames via
    :meth:`push_event`. The shape mirrors PR-9's production
    ``FakeHermes`` fixture but adds JSON-RPC affordances the chat
    round-trip path needs.

    Bound to a free 127.0.0.1 port so each test gets its own server +
    no port-collision risk in CI's parallel runs.
    """

    def __init__(self) -> None:
        self.app = FastAPI()
        self._events_ws: WebSocket | None = None
        self._events_signal = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.events_inbound_headers: dict[str, str] = {}
        self.ws_inbound_frames: list[str] = []
        self.rest_calls: list[tuple[str, dict[str, Any]]] = []
        # Default REST response — overridable per-test by setting
        # ``rest_response`` BEFORE the route hits.
        self.rest_response: dict[str, Any] = {
            "jsonrpc": "2.0",
            "result": {"ok": True, "session_id": "test-session-1"},
            "id": 1,
        }
        self._register()

    def _register(self) -> None:
        @self.app.websocket("/api/events")
        async def events_endpoint(ws: WebSocket) -> None:
            self.events_inbound_headers = dict(ws.headers)
            await ws.accept()
            self._events_ws = ws
            self._loop = asyncio.get_running_loop()
            self._events_signal.set()
            try:
                while True:
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass

        @self.app.websocket("/api/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            await ws.accept()
            try:
                while True:
                    raw = await ws.receive_text()
                    self.ws_inbound_frames.append(raw)
                    # Echo a JSON-RPC ack so the proxy has something to
                    # forward back to the browser.
                    await ws.send_text(
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ack": True}})
                    )
            except WebSocketDisconnect:
                pass

        @self.app.post("/api/{method:path}")
        async def rest_method(method: str, body: dict[str, Any]) -> dict[str, Any]:
            self.rest_calls.append((method, body))
            return self.rest_response

    async def push_event(self, frame: str) -> None:
        """Push one event frame from upstream toward the proxy."""
        assert self._events_ws is not None, "events WS not connected yet"
        await self._events_ws.send_text(frame)

    async def wait_for_events_connection(self, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._events_signal.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise AssertionError(
                "proxy never connected to fake hermes /api/events within timeout"
            ) from exc


def _free_port() -> int:
    """Grab an ephemeral port the OS isn't already using."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _ServerThread(threading.Thread):
    """Run uvicorn in a daemon thread for the fake hermes server."""

    def __init__(self, app: FastAPI, port: int) -> None:
        super().__init__(daemon=True)
        self._config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
        self._server = uvicorn.Server(self._config)

    def run(self) -> None:
        self._server.run()

    def stop(self) -> None:
        self._server.should_exit = True


@pytest.fixture
def fake_hermes(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeWsServer]:
    """Spin up a FakeWsServer on a free 127.0.0.1 port + steer the
    chat-proxy at it via env vars."""
    port = _free_port()
    hermes = FakeWsServer()
    server = _ServerThread(hermes.app, port)
    server.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover — fixture sanity
        server.stop()
        raise RuntimeError("fake hermes didn't start")

    monkeypatch.setenv("HAL0_HERMES_HOST", "127.0.0.1")
    monkeypatch.setenv("HAL0_HERMES_PORT", str(port))

    try:
        yield hermes
    finally:
        server.stop()
        server.join(timeout=2.0)


@pytest.fixture
def harness_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient pointed at a fresh hal0-api with isolated state.

    Sets:
      * ``HAL0_AGENT_SECRET_PATH`` — fresh HMAC secret per test
      * ``HAL0_HOME`` — fresh /etc/hal0 + /var/lib/hal0 surrogate
      * ``HAL0_ALLOWED_ORIGINS`` — only 127.0.0.1:8080 (tight default)
      * ``HAL0_HERMES_RUNTIME_JSON`` — a chmod-0600 file the proxy reads
    """
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    (tmp_path / "hal0_home" / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")

    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"host": "127.0.0.1", "port": 9119, "token": ""}))
    runtime.chmod(0o600)
    monkeypatch.setenv("HAL0_HERMES_RUNTIME_JSON", str(runtime))

    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authorised_client(harness_client: TestClient) -> TestClient:
    """A TestClient with a valid HMAC session cookie attached."""
    from hal0.api.agents import _auth

    resp = harness_client.get("/api/agents/hermes/session/handshake")
    assert resp.status_code == 200, resp.text
    cookie = resp.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert cookie is not None
    harness_client.cookies.set(_auth.SESSION_COOKIE_NAME, cookie)
    return harness_client
