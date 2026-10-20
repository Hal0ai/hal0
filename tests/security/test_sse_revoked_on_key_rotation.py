"""Open streaming responses do not outlive the credential that opened them (#2816).

The enforcement middleware checks a request's credential once, when it
arrives. A ``text/event-stream`` response then lives for hours, so before
#2816 a key rotation -- the operator's response to a compromise -- left every
already-open admin event or log stream reading on. #2801 closed the same gap
for WebSockets; this is the HTTP half, on the same registry.

``TestClient`` buffers a whole response before returning it, so it cannot
hold an endless stream open. These tests run the real app under a real
uvicorn server on a loopback port, open a stream with ``httpx``, rotate
through ``POST /api/auth/rotate``, and watch the stream end.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from hal0.api import auth as auth_mod
from hal0.api import create_app, live_sockets
from hal0.api.agents import _auth
from hal0.dispatcher.router import Dispatcher
from hal0.upstreams.registry import Upstream, UpstreamRegistry

ADMIN_KEY = "test-admin-key-2816"
CLIENT_KEY = "test-client-key-2816"
REVOKED_LINES = ["event: revoked", 'data: {"reason": "credential revoked"}']
CLIENT_STREAM_PATH = "/v1/_test-2816/stream"
_DEADLINE_S = 10.0


@dataclass
class Served:
    url: str
    app: FastAPI
    stream_finished: threading.Event


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


def _serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, require_auth: bool
) -> Iterator[Served]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1" if require_auth else "0")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    auth_mod._require_auth_cache = None
    app = create_app()

    # A client-tier stream: ``/v1`` is CLIENT-classified, and the real ones
    # (streamed completions) need a model backend. Its ``finally`` proves the
    # generator was closed, not abandoned.
    stream_finished = threading.Event()

    async def _client_stream() -> StreamingResponse:
        async def gen() -> Any:
            try:
                while True:
                    yield "data: tick\n\n"
                    await asyncio.sleep(0.05)
            finally:
                stream_finished.set()

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.add_api_route(CLIENT_STREAM_PATH, _client_stream, methods=["GET"])

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Served(f"http://127.0.0.1:{port}", app, stream_finished)
    finally:
        server.should_exit = True
        thread.join(15)
        auth_mod._require_auth_cache = None


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    yield from _serve(tmp_path, monkeypatch, require_auth=True)


@pytest.fixture
def served_auth_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    yield from _serve(tmp_path, monkeypatch, require_auth=False)


def _cookie_header(value: str) -> dict[str, str]:
    return {"cookie": f"{_auth.SESSION_COOKIE_NAME}={value}"}


def _sign_in(served: Served) -> dict[str, str]:
    resp = httpx.post(f"{served.url}/api/auth/login", json={"key": ADMIN_KEY})
    assert resp.status_code == 200, resp.text
    value = resp.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert value
    return _cookie_header(value)


def _short_lived_cookie(ttl_seconds: int) -> dict[str, str]:
    value = _auth.set_session_cookie(Response(), ttl_seconds=ttl_seconds)
    return _cookie_header(value)


def _rotate(served: Served, **body: Any) -> None:
    resp = httpx.post(
        f"{served.url}/api/auth/rotate",
        json=body,
        headers={"Authorization": f"Bearer {os.environ['HAL0_ADMIN_KEY']}"},
        timeout=_DEADLINE_S,
    )
    assert resp.status_code == 200, resp.text


@contextlib.contextmanager
def _open_stream(served: Served, path: str, headers: dict[str, str]) -> Iterator[Iterator[str]]:
    """Open ``path`` as a stream; yield its line iterator once headers arrived.

    A stream that fails to end never hangs the test: every read has a
    timeout, and the caller's loop is bounded by :data:`_DEADLINE_S`.
    """
    with (
        httpx.Client(timeout=httpx.Timeout(_DEADLINE_S, read=_DEADLINE_S)) as client,
        client.stream("GET", f"{served.url}{path}", headers=headers) as resp,
    ):
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("text/event-stream")
        yield resp.iter_lines()


def _read_to_end(lines: Iterator[str]) -> list[str]:
    """Read until the server ends the stream; fail if it is still going at the deadline."""
    seen: list[str] = []
    deadline = time.monotonic() + _DEADLINE_S
    for line in lines:
        seen.append(line)
        assert time.monotonic() < deadline, f"stream still open after revocation: {seen[-5:]}"
    return seen


def _wait_for(predicate: Any, what: str) -> None:
    deadline = time.monotonic() + _DEADLINE_S
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def _subscribers(served: Served) -> int:
    return len(served.app.state.events.subscribers)


def _assert_released(served: Served, baseline: int) -> None:
    """Nothing the stream held outlives it: no registry entry, no bus subscriber.

    ``baseline`` is the bus's subscriber count before the stream opened (the
    app subscribes internal consumers of its own).
    """
    _wait_for(lambda: live_sockets.open_count() == 0, "stream left registered")
    _wait_for(lambda: _subscribers(served) == baseline, "event-bus subscriber leaked")


def _assert_ends_revoked(lines: Iterator[str]) -> None:
    seen = _read_to_end(lines)
    assert seen[-3:-1] == REVOKED_LINES, seen[-5:]


# ---------------------------------------------------------------------------
# Admin streams: cookie and admin key.


def test_event_stream_ends_when_rotation_signs_sessions_out(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signed-in browser's event stream must not survive the sign-out.

    It must end through the graceful path (the app's ``receive()`` wakes with
    a disconnect, and Starlette closes the generator), not by the drain
    timeout's cancel: that timeout is made far longer than the bound here.
    """
    monkeypatch.setattr(live_sockets, "STREAM_DRAIN_TIMEOUT_S", 60.0)
    cookie = _sign_in(served)
    baseline = _subscribers(served)
    with _open_stream(served, "/api/events/stream", cookie) as lines:
        _rotate(served, tier="admin")
        revoked_at = time.monotonic()

        _assert_ends_revoked(lines)
        assert time.monotonic() - revoked_at < 3.0
    _assert_released(served, baseline)


def test_event_stream_survives_rotation_that_keeps_sessions(served: Served) -> None:
    """``keep_sessions`` leaves the cookie valid, so the stream stays open."""
    cookie = _sign_in(served)
    baseline = _subscribers(served)
    with _open_stream(served, "/api/events/stream", cookie):
        _rotate(served, tier="admin", keep_sessions=True)

        assert live_sockets.revalidate() == 0
        assert live_sockets.open_count() == 1
        assert _subscribers(served) == baseline + 1
    _assert_released(served, baseline)


@pytest.mark.parametrize("keep_sessions", [False, True])
def test_admin_key_stream_ends_on_admin_rotation(served: Served, keep_sessions: bool) -> None:
    """``keep_sessions`` keeps cookies, never a stream on the replaced key."""
    baseline = _subscribers(served)
    with _open_stream(served, f"/api/events/stream?api_key={ADMIN_KEY}", {}) as lines:
        _rotate(served, tier="admin", keep_sessions=keep_sessions)

        _assert_ends_revoked(lines)
    _assert_released(served, baseline)


def test_journal_stream_ends_on_admin_rotation(served: Served) -> None:
    """The guard is generic: a second admin feed needs no code of its own."""
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    with _open_stream(served, "/api/journal/stream", headers) as lines:
        _rotate(served, tier="admin", keep_sessions=True)

        _assert_ends_revoked(lines)
    _wait_for(lambda: live_sockets.open_count() == 0, "stream left registered")


# ---------------------------------------------------------------------------
# Client-tier streams.


def test_client_stream_ends_when_client_key_rotates(served: Served) -> None:
    headers = {"Authorization": f"Bearer {CLIENT_KEY}"}
    with _open_stream(served, CLIENT_STREAM_PATH, headers) as lines:
        assert next(lines) == "data: tick"

        _rotate(served, tier="client")

        _assert_ends_revoked(lines)
    _wait_for(served.stream_finished.is_set, "stream generator was not closed")
    _wait_for(lambda: live_sockets.open_count() == 0, "stream left registered")


def test_client_stream_on_a_surviving_key_keeps_streaming(served: Served) -> None:
    """Rotating the admin key leaves a client-key stream alone."""
    headers = {"Authorization": f"Bearer {CLIENT_KEY}"}
    with _open_stream(served, CLIENT_STREAM_PATH, headers) as lines:
        assert next(lines) == "data: tick"

        _rotate(served, tier="admin")

        for _ in range(6):  # well past the rotation: still ticking
            line = next(lines)
            assert line in ("data: tick", ""), line
        assert live_sockets.open_count() == 1
    _wait_for(served.stream_finished.is_set, "stream generator was not closed")


# ---------------------------------------------------------------------------
# Auth off, and session expiry.


def test_stream_is_not_revoked_while_auth_is_off(served_auth_off: Served) -> None:
    """Dev-open: nothing was checked at open, so nothing is re-checked."""
    served = served_auth_off
    with _open_stream(served, CLIENT_STREAM_PATH, {}) as lines:
        assert next(lines) == "data: tick"
        assert live_sockets.open_count() == 0

        _rotate(served, tier="admin")

        for _ in range(6):
            line = next(lines)
            assert line in ("data: tick", ""), line


def test_event_stream_ends_when_its_session_expires(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process-wide ticker ends a stream whose cookie expired, no rotation needed."""
    monkeypatch.setattr(live_sockets, "REVALIDATE_INTERVAL_S", 0.1)
    live_sockets._ticker_wake.set()  # a ticker already sleeping picks up the new interval
    baseline = _subscribers(served)
    with _open_stream(served, "/api/events/stream", _short_lived_cookie(2)) as lines:
        _wait_for(lambda: live_sockets.open_count() == 1, "stream not registered")

        _assert_ends_revoked(lines)
    _assert_released(served, baseline)


# ---------------------------------------------------------------------------
# The ASGI guard itself, against raw apps.


_SSE_START = {
    "type": "http.response.start",
    "status": 200,
    "headers": [(b"content-type", b"text/event-stream")],
}


async def _drive(app: Any, still_authorized: Any, revoke_after: int) -> tuple[list[dict], set]:
    """Run ``app`` under the guard; revoke once ``revoke_after`` frames went out."""
    sent: list[dict] = []
    allowed = True
    frames = asyncio.Event()

    async def receive() -> dict:
        await asyncio.Event().wait()  # the client never leaves on its own
        raise AssertionError("unreachable")

    async def send(message: dict) -> None:
        sent.append(message)
        if sum(1 for m in sent if m.get("more_body")) >= revoke_after:
            frames.set()

    async def rotate() -> None:
        nonlocal allowed
        await frames.wait()
        allowed = False
        live_sockets.revalidate()

    rotation = asyncio.ensure_future(rotate())
    before = asyncio.all_tasks()
    await live_sockets.guard_http_stream(
        app, {"type": "http", "path": "/x"}, receive, send, lambda: allowed and still_authorized()
    )
    rotation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await rotation
    leftover = {t for t in asyncio.all_tasks() - before if not t.done()}
    return sent, leftover


def _assert_revoked_end(sent: list[dict]) -> None:
    assert sent[-1] == {
        "type": "http.response.body",
        "body": live_sockets.REVOKED_SSE_EVENT,
        "more_body": False,
    }
    assert sum(1 for m in sent if m.get("more_body") is False) == 1


def test_guard_cancels_a_stream_that_ignores_the_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app that never reads ``receive`` is cancelled after the drain timeout, quietly."""
    monkeypatch.setattr(live_sockets, "STREAM_DRAIN_TIMEOUT_S", 0.1)
    closed = False

    async def stubborn(scope: Any, receive: Any, send: Any) -> None:
        nonlocal closed
        await send(_SSE_START)
        await send({"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True})
        try:
            await asyncio.Event().wait()
        finally:
            closed = True

    sent, leftover = asyncio.run(_drive(stubborn, lambda: True, revoke_after=1))
    _assert_revoked_end(sent)
    assert closed
    assert not leftover
    assert live_sockets.open_count() == 0


def test_guard_stops_a_producer_and_swallows_its_error() -> None:
    """A producer that keeps sending gets an OSError, not a crash in the server."""

    async def producer(scope: Any, receive: Any, send: Any) -> None:
        await send(_SSE_START)
        while True:
            await send({"type": "http.response.body", "body": b"data: x\n\n", "more_body": True})
            await asyncio.sleep(0)

    sent, leftover = asyncio.run(_drive(producer, lambda: True, revoke_after=3))
    _assert_revoked_end(sent)
    assert sum(1 for m in sent if m.get("more_body")) == 3  # nothing after the revocation
    assert not leftover
    assert live_sockets.open_count() == 0


def test_guard_revokes_when_the_check_raises() -> None:
    """Fail safe: a predicate that raises ends the stream."""

    def broken() -> bool:
        raise RuntimeError("config unreadable")

    async def feed(scope: Any, receive: Any, send: Any) -> None:
        await send(_SSE_START)
        await receive()  # wakes with http.disconnect once revoked

    sent, leftover = asyncio.run(_drive(feed, broken, revoke_after=0))
    _assert_revoked_end(sent)
    assert not leftover


def test_guard_leaves_a_plain_response_alone() -> None:
    """Only ``text/event-stream`` is registered; a chunked JSON body is not."""

    async def chunked(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        assert live_sockets.open_count() == 0
        await send({"type": "http.response.body", "body": b"{", "more_body": True})
        assert live_sockets.open_count() == 0
        await send({"type": "http.response.body", "body": b"}", "more_body": False})

    sent, _ = asyncio.run(_drive(chunked, lambda: False, revoke_after=99))
    assert [m.get("body") for m in sent[1:]] == [b"{", b"}"]


# ---------------------------------------------------------------------------
# A real ``/v1`` ``stream: true`` relay through the dispatcher.


class _Registry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)

    def __getitem__(self, name: str) -> Upstream:
        return self._store[name]


@pytest.mark.parametrize("upstream_type", ["text/event-stream", "application/octet-stream"])
def test_v1_stream_relay_ends_when_client_key_rotates(
    served: Served, monkeypatch: pytest.MonkeyPatch, upstream_type: str
) -> None:
    """A streamed completion is revoked whatever content type the upstream sends.

    The relay passes the upstream's content type through, so coverage cannot
    rest on it: the ``/v1`` route marks the request instead.
    """
    monkeypatch.setenv("TEST_2816_UPSTREAM_KEY", "sk-upstream")
    model = "test-2816-model"
    upstream = Upstream(
        name="fake-upstream",
        kind="remote",
        url="https://upstream.invalid/v1",
        auth_value_env="TEST_2816_UPSTREAM_KEY",
    )
    upstream_closed = threading.Event()

    async def endless() -> Any:
        try:
            while True:
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                await asyncio.sleep(0.05)
        finally:
            upstream_closed.set()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless(), headers={"content-type": upstream_type})

    async def online(_u: Upstream) -> bool:
        return True

    served.app.state.dispatcher = Dispatcher(
        upstream_registry=_Registry([upstream]),
        is_online=online,
        cached_models=lambda name: [model] if name == upstream.name else [],
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    headers = {"Authorization": f"Bearer {CLIENT_KEY}"}
    body = {"model": model, "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    with (
        httpx.Client(timeout=httpx.Timeout(_DEADLINE_S, read=_DEADLINE_S)) as client,
        client.stream(
            "POST", f"{served.url}/v1/chat/completions", json=body, headers=headers
        ) as resp,
    ):
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith(upstream_type)
        lines = resp.iter_lines()
        assert next(lines).startswith("data: ")
        assert live_sockets.open_count() == 1

        _rotate(served, tier="client")

        seen = _read_to_end(lines)
        if upstream_type == "text/event-stream":
            assert seen[-3:-1] == REVOKED_LINES, seen[-5:]
        else:
            assert "event: revoked" not in seen
    _wait_for(upstream_closed.is_set, "upstream stream was not closed")
    _wait_for(lambda: live_sockets.open_count() == 0, "stream left registered")


# ---------------------------------------------------------------------------
# The ticker forgives one raising check; a rotation never does.


def test_tick_revokes_only_after_consecutive_check_errors() -> None:
    """One raising tick is forgiven, and a passing tick resets the count."""

    async def _run() -> list[bool]:
        plan: list[bool] = []  # True = raise; empty = pass (registration)

        def check() -> bool:
            if plan and plan.pop(0):
                raise OSError("secret.bin: Too many open files")
            return True

        observed: list[bool] = []
        with live_sockets.track(check) as revoked:
            plan.extend([True, False, True, True])  # raise, pass, raise, raise
            for _ in range(4):
                live_sockets.revalidate(tick=True)
                observed.append(revoked.is_set())
        return observed

    assert asyncio.run(_run()) == [False, False, False, True]
    assert live_sockets.open_count() == 0


def test_rotation_revokes_on_the_first_check_error() -> None:
    async def _run() -> bool:
        broken = False

        def check() -> bool:
            if broken:
                raise OSError("secret.bin: Too many open files")
            return True

        with live_sockets.track(check) as revoked:
            broken = True
            assert live_sockets.revalidate() == 1
            return revoked.is_set()

    assert asyncio.run(_run()) is True
