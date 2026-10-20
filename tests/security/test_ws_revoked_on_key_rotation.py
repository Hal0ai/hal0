"""Open WebSockets do not outlive the credential that opened them (#2801).

Every WebSocket gate in hal0 (the enforcement middleware and the chat-proxy
cookie gate) checks the credential once, at the upgrade. Before #2801 nothing
looked again, so a key rotation -- the operator's response to a compromise --
left every already-open socket working: an attacker holding an open
``/api/agents/{id}/submit`` socket kept sending ``prompt.submit`` past the
sign-out, and a bearer on an open ``/v1/realtime`` socket kept talking after
its key was replaced.

These tests drive the real app: open a socket, rotate through
``POST /api/auth/rotate``, then prove the socket is closed with 4401 and that
nothing sent after the rotation reaches the upstream.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hal0.api import auth as auth_mod
from hal0.api import create_app, live_sockets
from hal0.api.agents import _auth, chat_proxy
from tests.realtime.conftest import default_backends

ORIGIN = {"origin": "http://127.0.0.1:8080"}
ADMIN_KEY = "test-admin-key-2801"
CLIENT_KEY = "test-client-key-2801"
_SESSION_UPDATE = json.dumps({"type": "session.update", "session": {}})


@pytest.fixture(autouse=True)
def _restore_key_env() -> Iterator[None]:
    """``rotate_api_env_key`` writes ``os.environ`` directly; put it back."""
    saved = {k: os.environ.get(k) for k in ("HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY")}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeUpstream:
    """Stands in for the hermes WS: records frames and echoes each one back."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(raw)
        await self._queue.put(json.dumps({"echo": raw}))

    async def recv(self) -> str:
        return await self._queue.get()

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def upstreams(monkeypatch: pytest.MonkeyPatch) -> list[_FakeUpstream]:
    made: list[_FakeUpstream] = []

    async def _connect(_url: str, **_kwargs: Any) -> _FakeUpstream:
        up = _FakeUpstream()
        made.append(up)
        return up

    monkeypatch.setattr(chat_proxy.websockets, "connect", _connect)
    return made


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    auth_mod._require_auth_cache = None
    app = create_app()
    app.state.realtime_backends = default_backends()
    with TestClient(app) as c:
        yield c
    auth_mod._require_auth_cache = None


def _sign_in(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"key": ADMIN_KEY})
    assert resp.status_code == 200, resp.text
    assert client.cookies.get(_auth.SESSION_COOKIE_NAME)


def _rotate(client: TestClient, **body: Any) -> None:
    resp = client.post(
        "/api/auth/rotate",
        json=body,
        headers={"Authorization": f"Bearer {os.environ['HAL0_ADMIN_KEY']}"},
    )
    assert resp.status_code == 200, resp.text


def _assert_closed_revoked(ws: Any, probe: str = "after") -> None:
    """Send ``probe`` and expect a 4401 close rather than its answer.

    Sending first keeps a regression from hanging: a socket that survived the
    rotation answers the probe, which fails the assertion instead of blocking.
    """
    ws.send_text(probe)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()
    assert exc_info.value.code == live_sockets.CLOSE_CREDENTIAL_REVOKED


@pytest.mark.parametrize("path", ["submit", "events"])
def test_agent_socket_is_closed_when_rotation_signs_sessions_out(
    app_client: TestClient, upstreams: list[_FakeUpstream], path: str
) -> None:
    """The issue's repro: an open agent socket must not keep driving hermes."""
    _sign_in(app_client)
    with app_client.websocket_connect(f"/api/agents/hermes/{path}", headers=ORIGIN) as ws:
        ws.send_text("before")
        assert json.loads(ws.receive_text()) == {"echo": "before"}

        _rotate(app_client, tier="admin")

        _assert_closed_revoked(ws)

    (upstream,) = upstreams
    assert upstream.sent == ["before"]
    assert upstream.closed is True


def test_agent_socket_survives_rotation_that_keeps_sessions(
    app_client: TestClient, upstreams: list[_FakeUpstream]
) -> None:
    """``keep_sessions`` leaves the cookie valid, so the socket stays open."""
    _sign_in(app_client)
    with app_client.websocket_connect("/api/agents/hermes/submit", headers=ORIGIN) as ws:
        _rotate(app_client, tier="admin", keep_sessions=True)
        ws.send_text("after")
        assert json.loads(ws.receive_text()) == {"echo": "after"}


def test_agent_submit_rechecks_the_cookie_per_frame(
    app_client: TestClient, upstreams: list[_FakeUpstream]
) -> None:
    """A secret replaced outside this process (the CLI fallback) is caught on the next frame."""
    _sign_in(app_client)
    with app_client.websocket_connect("/api/agents/hermes/submit", headers=ORIGIN) as ws:
        ws.send_text("before")
        assert json.loads(ws.receive_text()) == {"echo": "before"}

        assert _auth.regenerate_secret() is True  # no in-process revalidate call

        _assert_closed_revoked(ws)

    assert upstreams[0].sent == ["before"]


def test_realtime_client_bearer_is_closed_when_client_key_rotates(
    app_client: TestClient,
) -> None:
    """A bearer on an open realtime socket stops when its key is replaced."""
    with app_client.websocket_connect(
        "/v1/realtime?model=gpt-test", headers={"Authorization": f"Bearer {CLIENT_KEY}"}
    ) as ws:
        assert ws.receive_json()["type"] == "session.created"

        _rotate(app_client, tier="client")

        _assert_closed_revoked(ws, _SESSION_UPDATE)


def test_realtime_admin_bearer_is_closed_even_when_sessions_are_kept(
    app_client: TestClient,
) -> None:
    """``keep_sessions`` keeps cookies, never a bearer on the replaced key."""
    with app_client.websocket_connect(f"/v1/realtime?model=gpt-test&api_key={ADMIN_KEY}") as ws:
        assert ws.receive_json()["type"] == "session.created"

        _rotate(app_client, tier="admin", keep_sessions=True)

        _assert_closed_revoked(ws, _SESSION_UPDATE)


def test_realtime_socket_on_a_surviving_key_stays_open(app_client: TestClient) -> None:
    """Rotating the admin key leaves a client-key socket alone."""
    with app_client.websocket_connect(
        "/v1/realtime?model=gpt-test", headers={"Authorization": f"Bearer {CLIENT_KEY}"}
    ) as ws:
        assert ws.receive_json()["type"] == "session.created"
        _rotate(app_client, tier="admin")
        ws.send_text(_SESSION_UPDATE)
        assert ws.receive_json()["type"] == "session.updated"

    assert live_sockets.open_count() == 0


# ---------------------------------------------------------------------------
# Dev-open posture: the enforcement middleware waves every request through,
# but the chat proxy still gates on its own cookie, so it must also drop the
# socket by itself when that cookie stops verifying.


@pytest.fixture
def open_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    auth_mod._require_auth_cache = None
    with TestClient(create_app()) as c:
        assert auth_mod.require_auth_enabled() is False
        resp = c.get("/api/agents/hermes/session/handshake")
        assert resp.status_code == 200, resp.text
        yield c
    auth_mod._require_auth_cache = None


@pytest.mark.parametrize("path", ["submit", "events"])
def test_agent_socket_is_closed_on_rotation_with_auth_off(
    open_client: TestClient, upstreams: list[_FakeUpstream], path: str
) -> None:
    with open_client.websocket_connect(f"/api/agents/hermes/{path}", headers=ORIGIN) as ws:
        ws.send_text("before")
        assert json.loads(ws.receive_text()) == {"echo": "before"}

        resp = open_client.post("/api/auth/rotate", json={"tier": "admin"})
        assert resp.status_code == 200, resp.text

        _assert_closed_revoked(ws)

    assert upstreams[0].sent == ["before"]
    assert upstreams[0].closed is True
    assert live_sockets.open_count() == 0


def test_agent_submit_rechecks_the_cookie_per_frame_with_auth_off(
    open_client: TestClient, upstreams: list[_FakeUpstream]
) -> None:
    with open_client.websocket_connect("/api/agents/hermes/submit", headers=ORIGIN) as ws:
        ws.send_text("before")
        assert json.loads(ws.receive_text()) == {"echo": "before"}

        assert _auth.regenerate_secret() is True

        _assert_closed_revoked(ws)

    assert upstreams[0].sent == ["before"]


def test_revalidate_treats_a_failing_check_as_revoked() -> None:
    """Deny by default: a predicate that raises revokes its socket."""

    async def _run() -> bool:
        broken = False

        def _check() -> bool:
            if broken:
                raise RuntimeError("config unreadable")
            return True

        with live_sockets.track(_check) as revoked:
            assert not revoked.is_set()
            broken = True
            assert live_sockets.revalidate() == 1
            return revoked.is_set()

    assert asyncio.run(_run()) is True
    assert live_sockets.open_count() == 0


def test_track_checks_once_on_entry() -> None:
    """A credential that already fails when the socket registers is revoked at once."""

    async def _run() -> bool:
        with live_sockets.track(lambda: False) as revoked:
            return revoked.is_set()

    assert asyncio.run(_run()) is True
    assert live_sockets.open_count() == 0


# ---------------------------------------------------------------------------
# Round-2 review regressions.


class _ScriptedUpstream(_FakeUpstream):
    """A fake hermes that sends ``frames`` first, then echoes like its parent.

    ``drained`` is set once the proxy asks for the frame after the script,
    i.e. once every scripted frame has been handed to the browser-side sink.
    """

    def __init__(self, frames: list[str]) -> None:
        super().__init__()
        self._script = list(frames)
        self.drained = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def recv(self) -> str:
        if self._script:
            return self._script.pop(0)
        self._loop = asyncio.get_running_loop()
        self.drained.set()
        return await super().recv()

    def push(self, raw: str) -> None:
        """Queue a frame from the test thread (the proxy runs on the portal's loop)."""
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._queue.put_nowait, raw)


def _connect_with(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[str],
    during_connect: Callable[[], None] | None = None,
) -> list[_ScriptedUpstream]:
    made: list[_ScriptedUpstream] = []

    async def _connect(_url: str, **_kwargs: Any) -> _ScriptedUpstream:
        if during_connect is not None:
            during_connect()
        up = _ScriptedUpstream(frames)
        made.append(up)
        return up

    monkeypatch.setattr(chat_proxy.websockets, "connect", _connect)
    return made


def _rotate_in_process() -> None:
    """What ``POST /api/auth/rotate`` does to the sockets: new secret, then revalidate."""
    assert _auth.regenerate_secret() is True
    live_sockets.revalidate()


@pytest.mark.parametrize("path", ["submit", "events"])
def test_agent_socket_rotated_during_upstream_connect_is_closed(
    open_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A rotation that lands while the proxy dials hermes still reaches the socket.

    The browser sends nothing: ``/events`` never gets browser frames, so the
    per-frame re-check cannot be what closes it. Before the fix the socket
    registered only after the connect, ``revalidate`` could not see it, and
    the upstream's first frame went to a signed-out browser.
    """
    made = _connect_with(monkeypatch, ['{"hello": 1}'], during_connect=_rotate_in_process)
    with open_client.websocket_connect(f"/api/agents/hermes/{path}", headers=ORIGIN) as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == live_sockets.CLOSE_CREDENTIAL_REVOKED

    assert made[0].sent == []
    assert made[0].closed is True
    assert live_sockets.open_count() == 0


def test_revoked_events_socket_drops_buffered_progress(
    open_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revocation must not flush coalesced ``tool.progress`` to a signed-out browser."""
    monkeypatch.setattr(chat_proxy, "PROGRESS_COALESCE_SECONDS", 30.0)
    progress = json.dumps(
        {
            "method": "event",
            "params": {"type": "tool.progress", "payload": {"tool_id": "t1"}},
        }
    )
    made = _connect_with(monkeypatch, [progress])
    with open_client.websocket_connect("/api/agents/hermes/events", headers=ORIGIN) as ws:
        assert made[0].drained.wait(timeout=5)  # progress is now buffered

        resp = open_client.post("/api/auth/rotate", json={"tier": "admin"})
        assert resp.status_code == 200, resp.text

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == live_sockets.CLOSE_CREDENTIAL_REVOKED


def test_events_socket_rechecks_the_cookie_without_browser_frames(
    open_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret replaced outside this process closes an idle ``/events`` socket.

    ``hal0 auth reset-key`` falls back to writing the secret itself when the
    API call fails, so no in-process ``revalidate`` runs; ``/events`` gets no
    browser frames, so only the periodic re-check can catch it. The upstream
    keeps sending: before the fix those frames kept reaching the browser.
    """
    monkeypatch.setattr(chat_proxy, "SESSION_RECHECK_SECONDS", 0.05)
    made = _connect_with(monkeypatch, [])
    with open_client.websocket_connect("/api/agents/hermes/events", headers=ORIGIN) as ws:
        assert made[0].drained.wait(timeout=5)
        assert _auth.regenerate_secret() is True  # no in-process revalidate call
        time.sleep(0.5)
        made[0].push('{"late": 1}')  # would reach a revoked browser

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == live_sockets.CLOSE_CREDENTIAL_REVOKED


def test_rotation_counts_a_doubly_registered_agent_socket_once(
    app_client: TestClient, upstreams: list[_FakeUpstream], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With enforcement on, the middleware and the proxy both track one agent socket.

    ``sockets_closed`` in the ``hal0.auth.key_rotated`` log is
    ``revalidate()``'s return value, so it must count sockets, not registrations.
    """
    counts: list[int] = []
    real_revalidate = live_sockets.revalidate

    def _counting() -> int:
        counts.append(real_revalidate())
        return counts[-1]

    monkeypatch.setattr(live_sockets, "revalidate", _counting)
    _sign_in(app_client)
    with app_client.websocket_connect("/api/agents/hermes/submit", headers=ORIGIN) as ws:
        ws.send_text("before")
        assert json.loads(ws.receive_text()) == {"echo": "before"}
        assert live_sockets.open_count() == 2  # middleware guard + proxy

        _rotate(app_client, tier="admin")

        _assert_closed_revoked(ws)

    assert counts == [1]
