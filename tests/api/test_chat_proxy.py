"""Functional tests for the chat-proxy WS + REST surface.

A small in-process fake hermes server is spun up on a random loopback
port for each test. The chat-proxy is pointed at it via the same env
overrides the production code reads. That way we exercise the real
proxy plumbing (WS bridge, header injection, coalescer, REST shim)
without needing an actual hermes binary.
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
from fastapi import FastAPI, Header, HTTPException, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hal0.api.agents import _auth
from hal0.api.agents.chat_proxy import (
    PROGRESS_COALESCE_SECONDS,
    ProgressCoalescer,
)

# ---------------------------------------------------------------------------
# Fake hermes server. Captures what we want to assert about and lets the
# test send canned event frames out to the proxy.


class FakeHermes:
    """In-process stand-in for the hermes dashboard runtime.

    Implements just enough of hermes's WS + REST surface that the proxy
    can talk to it: ``/api/events``, ``/api/ws``, plus ``/api/<method>``
    POST shims that return canned JSON-RPC results. Records every
    inbound header + body so tests can assert security invariants
    (Authorization header present, query string empty, etc.).
    """

    def __init__(self) -> None:
        self.app = FastAPI()
        self.events_inbound_headers: dict[str, str] = {}
        self.events_inbound_query: str | None = None
        self.events_outbound: list[str] = []
        self.ws_inbound_frames: list[str] = []
        self.rest_calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.rest_response: dict[str, Any] = {"jsonrpc": "2.0", "result": {"ok": True}, "id": 1}
        self._events_signal = asyncio.Event()
        self._events_ws: WebSocket | None = None
        self.expected_token: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._register()

    def _register(self) -> None:
        @self.app.websocket("/api/events")
        async def events_endpoint(ws: WebSocket) -> None:
            self.events_inbound_headers = dict(ws.headers)
            self.events_inbound_query = ws.url.query
            # Echo a 401 close when token is expected and missing/wrong;
            # mirrors hermes's _ws_auth_ok.
            if self.expected_token is not None:
                supplied = ws.headers.get("authorization", "")
                if supplied != f"Bearer {self.expected_token}":
                    await ws.close(code=4401)
                    return
            await ws.accept()
            self._events_ws = ws
            self._loop = asyncio.get_running_loop()
            self._events_signal.set()
            try:
                while True:
                    # Subscribers don't actually send anything.
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass

        @self.app.websocket("/api/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            if self.expected_token is not None:
                supplied = ws.headers.get("authorization", "")
                if supplied != f"Bearer {self.expected_token}":
                    await ws.close(code=4401)
                    return
            await ws.accept()
            try:
                while True:
                    raw = await ws.receive_text()
                    self.ws_inbound_frames.append(raw)
                    # Echo a fake JSON-RPC result so the proxy has
                    # something to forward back.
                    await ws.send_text(
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ack": True}})
                    )
            except WebSocketDisconnect:
                pass

        @self.app.post("/api/{method}")
        async def rest_method(
            method: str,
            body: dict[str, Any],
            authorization: str | None = Header(default=None),
            x_hal0_agent: str | None = Header(default=None),
        ) -> dict[str, Any]:
            self.rest_calls.append(
                (
                    method,
                    body,
                    {
                        "authorization": authorization or "",
                        "x-hal0-agent": x_hal0_agent or "",
                    },
                )
            )
            if self.expected_token is not None and authorization != f"Bearer {self.expected_token}":
                raise HTTPException(status_code=401, detail="bad token")
            return self.rest_response

    async def push_event(self, frame: str) -> None:
        """Send one event frame from upstream → proxy → browser."""
        assert self._events_ws is not None, "events WS not connected yet"
        await self._events_ws.send_text(frame)

    async def wait_for_events_connection(self, timeout: float = 2.0) -> None:
        try:
            await asyncio.wait_for(self._events_signal.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise AssertionError("proxy never connected to fake hermes /api/events") from exc


def _free_port() -> int:
    """Grab an ephemeral port the OS isn't already using."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _ServerThread(threading.Thread):
    """Runs uvicorn in a background thread for the fake hermes."""

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
def fake_hermes(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHermes]:
    """Spin up FakeHermes on a free port + point the proxy at it."""
    port = _free_port()
    hermes = FakeHermes()
    server = _ServerThread(hermes.app, port)
    server.start()
    # Wait for uvicorn to actually be listening.
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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Fresh app + isolated secret + tight origin allowlist."""
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    (tmp_path / "hal0_home" / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")

    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _authorise_client(client: TestClient) -> str:
    """Mint a session cookie via the handshake endpoint + attach it."""
    resp = client.get("/api/agents/hermes/session/handshake")
    assert resp.status_code == 200
    cookie = resp.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert cookie is not None
    client.cookies.set(_auth.SESSION_COOKIE_NAME, cookie)
    return cookie


def _write_runtime_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    """Drop a runtime.json with ``token`` + point the proxy at it."""
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"host": "127.0.0.1", "port": 9119, "token": token}))
    p.chmod(0o600)
    monkeypatch.setenv("HAL0_HERMES_RUNTIME_JSON", str(p))


# ---------------------------------------------------------------------------
# End-to-end WS mirror


def test_events_ws_mirrors_frames(
    client: TestClient,
    fake_hermes: FakeHermes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame the fake hermes emits arrives byte-identical at the browser."""
    _authorise_client(client)
    _write_runtime_json(tmp_path, monkeypatch, "tok-mirror")
    fake_hermes.expected_token = "tok-mirror"

    canned = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "message.delta",
                "session_id": "s1",
                "payload": {"text": "hello"},
            },
        }
    )

    with client.websocket_connect(
        "/api/agents/hermes/events",
        headers={"origin": "http://127.0.0.1:8080"},
    ) as ws:
        # Wait for upstream to connect, then push.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and fake_hermes._events_ws is None:
            time.sleep(0.02)
        assert fake_hermes._events_ws is not None
        assert fake_hermes._loop is not None
        asyncio.run_coroutine_threadsafe(fake_hermes.push_event(canned), fake_hermes._loop).result(
            timeout=2.0
        )

        received = ws.receive_text()
        assert json.loads(received) == json.loads(canned)


def test_events_ws_injects_authorization_header(
    client: TestClient,
    fake_hermes: FakeHermes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outbound hop carries ``Authorization: Bearer <embed_token>``.

    AND the browser never sends one — that's the whole MUST-FIX #3
    point. We assert (1) hermes sees the header (2) the query string
    on the upstream hop is empty.
    """
    _authorise_client(client)
    _write_runtime_json(tmp_path, monkeypatch, "tok-secret-42")
    fake_hermes.expected_token = "tok-secret-42"

    with client.websocket_connect(
        "/api/agents/hermes/events",
        headers={
            "origin": "http://127.0.0.1:8080",
            # Browser tries to inject its own Authorization. The proxy
            # MUST overwrite this with the token from runtime.json.
            "authorization": "Bearer client-supplied-junk",
        },
    ):
        # Trigger a connect by waiting for fake hermes to see it.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and fake_hermes._events_ws is None:
            time.sleep(0.02)

    assert fake_hermes._events_ws is not None
    inbound = fake_hermes.events_inbound_headers
    assert inbound.get("authorization") == "Bearer tok-secret-42"
    assert inbound.get("x-hal0-agent") == "hermes"
    # Query string MUST be empty — the token must NOT leak there.
    assert fake_hermes.events_inbound_query in (None, "")
    # And the value of the Bearer must not be the client-supplied junk.
    assert "client-supplied-junk" not in str(inbound.values())


# ---------------------------------------------------------------------------
# Coalescer unit tests


@pytest.mark.asyncio
async def test_coalescer_buffers_progress_then_flushes() -> None:
    """N rapid tool.progress frames flush at most once after 100ms."""
    sent: list[str] = []

    async def sink(raw: str) -> None:
        sent.append(raw)

    coalescer = ProgressCoalescer(sink)
    for i in range(10):
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "tool.progress",
                    "session_id": "s",
                    "payload": {"tool_id": "t1", "preview": f"step {i}"},
                },
            }
        )
        await coalescer.handle(raw)

    # No flush has happened yet.
    assert sent == []
    # Wait for the timer to fire.
    await asyncio.sleep(PROGRESS_COALESCE_SECONDS * 2)
    # One flush, last value wins for tool_id=t1.
    assert len(sent) == 1
    payload = json.loads(sent[0])["params"]["payload"]
    assert payload["preview"] == "step 9"
    await coalescer.close()


@pytest.mark.asyncio
async def test_coalescer_non_progress_event_flushes_buffer_first() -> None:
    """A non-progress event drains the buffer + then forwards itself.

    Ordering invariant: progress must precede the following
    tool.complete.
    """
    sent: list[str] = []

    async def sink(raw: str) -> None:
        sent.append(raw)

    coalescer = ProgressCoalescer(sink)
    progress = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "tool.progress",
                "session_id": "s",
                "payload": {"tool_id": "t1", "preview": "halfway"},
            },
        }
    )
    complete = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "tool.complete",
                "session_id": "s",
                "payload": {"tool_id": "t1", "result_text": "done"},
            },
        }
    )

    await coalescer.handle(progress)
    await coalescer.handle(complete)

    # Both went out — progress first, complete second.
    assert len(sent) == 2
    assert json.loads(sent[0])["params"]["type"] == "tool.progress"
    assert json.loads(sent[1])["params"]["type"] == "tool.complete"
    await coalescer.close()


@pytest.mark.asyncio
async def test_coalescer_keeps_per_tool_id_separate() -> None:
    """Two distinct tool_ids both survive the coalescer."""
    sent: list[str] = []

    async def sink(raw: str) -> None:
        sent.append(raw)

    coalescer = ProgressCoalescer(sink)
    for tool_id in ("alpha", "beta"):
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "tool.progress",
                    "session_id": "s",
                    "payload": {"tool_id": tool_id, "preview": tool_id},
                },
            }
        )
        await coalescer.handle(raw)

    await asyncio.sleep(PROGRESS_COALESCE_SECONDS * 2)
    assert len(sent) == 2
    ids = {json.loads(f)["params"]["payload"]["tool_id"] for f in sent}
    assert ids == {"alpha", "beta"}
    await coalescer.close()


@pytest.mark.asyncio
async def test_coalescer_passes_through_unparseable_frames() -> None:
    """Garbage in → garbage straight through (don't drop frames)."""
    sent: list[str] = []

    async def sink(raw: str) -> None:
        sent.append(raw)

    coalescer = ProgressCoalescer(sink)
    await coalescer.handle("not even json")
    assert sent == ["not even json"]
    await coalescer.close()


# ---------------------------------------------------------------------------
# REST shim


def test_session_create_proxies_to_hermes(
    client: TestClient,
    fake_hermes: FakeHermes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /session/create → hermes JSON-RPC ``session.create``.

    Verifies (1) the right method gets invoked, (2) the Authorization
    header rides along, (3) the response shape is forwarded.
    """
    _authorise_client(client)
    _write_runtime_json(tmp_path, monkeypatch, "tok-rest")
    fake_hermes.expected_token = "tok-rest"
    fake_hermes.rest_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"session_id": "new-session-id", "model": "demo"},
    }

    resp = client.post(
        "/api/agents/hermes/session/create",
        json={"model": "demo"},
        headers={"origin": "http://127.0.0.1:8080"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"session_id": "new-session-id", "model": "demo"}

    assert len(fake_hermes.rest_calls) == 1
    method, payload, headers = fake_hermes.rest_calls[0]
    assert method == "session.create"
    # JSON-RPC envelope built by the proxy.
    assert payload["method"] == "session.create"
    assert payload["params"] == {"model": "demo"}
    assert headers["authorization"] == "Bearer tok-rest"
    assert headers["x-hal0-agent"] == "hermes"


def test_session_create_rejects_without_cookie(client: TestClient, fake_hermes: FakeHermes) -> None:
    """Even with hermes up, REST shim refuses unauthenticated calls."""
    resp = client.post("/api/agents/hermes/session/create", json={})
    assert resp.status_code == 403


def test_session_history_query_param_forwarded(
    client: TestClient,
    fake_hermes: FakeHermes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``session_id`` query string is forwarded as a JSON-RPC param.

    The token is NOT — it rides outbound in the header. We assert both.
    """
    _authorise_client(client)
    _write_runtime_json(tmp_path, monkeypatch, "tok-hist")
    fake_hermes.expected_token = "tok-hist"
    fake_hermes.rest_response = {"jsonrpc": "2.0", "id": 1, "result": {"messages": []}}

    resp = client.get("/api/agents/hermes/session/history?session_id=abc-123")
    assert resp.status_code == 200, resp.text
    method, payload, headers = fake_hermes.rest_calls[0]
    assert method == "session.history"
    assert payload["params"] == {"session_id": "abc-123"}
    assert headers["authorization"] == "Bearer tok-hist"


# ---------------------------------------------------------------------------
# Log scrubber


def test_log_scrubber_strips_query_string() -> None:
    """The QueryStringScrubber filter rewrites the request line.

    Direct unit test against the filter so we don't have to wrangle
    uvicorn's own log emission.
    """
    import logging

    from hal0.api.middleware.log_scrub import QueryStringScrubber

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s" %d',
        args=("127.0.0.1:12345", "GET /api/foo?token=SECRET&x=1 HTTP/1.1", 200),
        exc_info=None,
    )
    scrubber = QueryStringScrubber()
    assert scrubber.filter(record) is True
    assert record.args is not None
    assert "SECRET" not in record.args[1]  # type: ignore[index]
    assert "?" not in record.args[1]  # type: ignore[index]
    assert record.args[1] == "GET /api/foo HTTP/1.1"  # type: ignore[index]


def test_log_scrubber_no_query_unchanged() -> None:
    """A request line with no query string passes through unchanged."""
    import logging

    from hal0.api.middleware.log_scrub import QueryStringScrubber

    line = "GET /api/health HTTP/1.1"
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s" %d',
        args=("127.0.0.1:1", line, 200),
        exc_info=None,
    )
    QueryStringScrubber().filter(record)
    assert record.args is not None
    assert record.args[1] == line  # type: ignore[index]
