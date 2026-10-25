"""ComfyUI's editor through hal0: the authenticated reverse proxy under ``/comfyui/`` (#3049).

ComfyUI listens on loopback only, so hal0 serves its editor on hal0's own origin. These
tests drive the real app with a fake ComfyUI behind it:

- HTTP: an ``httpx`` transport stands in for ComfyUI and records what reaches it.
- WebSocket: the ``_ws_connect`` seam returns an echoing fake, and one test runs a real
  websocket server so the handshake headers are the ones on the wire.

Both fakes apply ComfyUI's own cross-site check (``_comfyui_refuses``, a port of
``origin_only_middleware`` in ComfyUI v0.28.0 ``server.py:159-197``), so every forwarded
request also proves the proxy satisfies it.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import inspect
import ipaddress
import json
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_client_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import InvalidStatus

from hal0.api import auth as auth_mod
from hal0.api.routes import comfyui_proxy
from hal0.mcp.admin import build_admin_route_map
from hal0.security.exposure import AuthClass, classify, match_rule

ALLOWED_ORIGIN = "http://127.0.0.1:8080"
FOREIGN_ORIGIN = "https://evil.example"
ADMIN_KEY = "test-admin-key-3049"
CLIENT_KEY = "test-client-key-3049"
UPSTREAM = "127.0.0.1:8188"


# ---------------------------------------------------------------------------
# ComfyUI's own cross-site check, ported so the fakes refuse what ComfyUI refuses


def _is_loopback_name(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _comfyui_refuses(headers: Any) -> bool:
    """True iff ComfyUI v0.28.0's ``origin_only_middleware`` answers this request 403.

    ``server.py:159-197``: a ``Sec-Fetch-Site: cross-site`` request is refused, and so is
    one whose ``Origin`` authority differs from its ``Host`` when that host is loopback.
    """
    if headers.get("sec-fetch-site") == "cross-site":
        return True
    host = headers.get("host")
    origin = headers.get("origin")
    if not host or not origin:
        return False
    host_domain = host.lower()
    parsed = urlparse(origin)
    origin_domain = parsed.netloc.lower()
    host_parsed = urlsplit("//" + host_domain)
    loopback = _is_loopback_name(host_parsed.hostname)
    if parsed.port is None:
        host_domain = host_parsed.hostname or ""
    if host_parsed.port is None:
        origin_domain = parsed.hostname or ""
    return loopback and bool(host_domain) and bool(origin_domain) and host_domain != origin_domain


def test_the_ported_check_refuses_a_browser_origin_sent_straight_to_comfyui() -> None:
    """Control: without the proxy's rewrite, ComfyUI would refuse the dashboard's own Origin."""
    assert _comfyui_refuses({"host": UPSTREAM, "origin": ALLOWED_ORIGIN})
    assert _comfyui_refuses({"host": UPSTREAM, "sec-fetch-site": "cross-site"})
    assert not _comfyui_refuses({"host": UPSTREAM, "origin": f"http://{UPSTREAM}"})
    assert not _comfyui_refuses({"host": UPSTREAM})


# ---------------------------------------------------------------------------
# Fake ComfyUI over HTTP


class _RawBody(httpx.AsyncByteStream):
    """A response body as a server sends it: raw bytes, read once, as a stream.

    ``httpx.Response(content=...)`` reads (and decodes) its body at construction, which
    no real transport does; the proxy relays the raw stream.
    """

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.body:
            yield self.body


def _reply(
    status: int,
    body: bytes = b"",
    headers: list[tuple[str, str]] | dict[str, str] | None = None,
    *,
    json_body: Any = None,
) -> httpx.Response:
    """What ComfyUI would answer: status, headers (Content-Length added) and a raw body."""
    items = list(headers.items()) if isinstance(headers, dict) else list(headers or [])
    if json_body is not None:
        body = json.dumps(json_body).encode()
        items.append(("content-type", "application/json"))
    items.append(("content-length", str(len(body))))
    return httpx.Response(status, headers=items, stream=_RawBody(body))


@dataclass
class Seen:
    method: str
    url: httpx.URL
    headers: httpx.Headers
    body: bytes


@dataclass
class FakeComfyUI:
    """Records every request that reaches 'ComfyUI' and answers with ``respond``."""

    requests: list[Seen] = field(default_factory=list)
    respond: Any = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.requests.append(Seen(request.method, request.url, request.headers, body))
        if _comfyui_refuses(request.headers):
            return _reply(403)
        if self.respond is not None:
            return self.respond(request)
        return _reply(200, b"comfyui", {"content-type": "text/plain"})


@pytest.fixture
def comfy(monkeypatch: pytest.MonkeyPatch) -> FakeComfyUI:
    fake = FakeComfyUI()
    monkeypatch.setattr(comfyui_proxy, "_client", None)
    monkeypatch.setattr(
        comfyui_proxy,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )
    return fake


class _StreamingTransport(httpx.AsyncBaseTransport):
    """Hands the request to ``handler`` unread (``MockTransport`` reads the body first)."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.handler(request)


def _use_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    monkeypatch.setattr(comfyui_proxy, "_client", None)
    monkeypatch.setattr(
        comfyui_proxy, "_build_client", lambda: httpx.AsyncClient(transport=transport)
    )


# ---------------------------------------------------------------------------
# Fake ComfyUI websocket


class FakeComfySocket:
    """An echoing stand-in for ComfyUI's ``/ws``: greets, echoes, closes on request."""

    GREETING = '{"type": "status", "data": {"sid": "fake"}}'

    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed_with: int | None = None
        self.close_code: int | None = None
        self.close_reason = ""
        self._queue: asyncio.Queue[Any] | None = None

    def _q(self) -> asyncio.Queue[Any]:
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._queue.put_nowait(self.GREETING)
        return self._queue

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        if message == "close please":
            self.close_code = 1000
            await self._q().put(None)
        else:
            await self._q().put(message)

    def __aiter__(self) -> FakeComfySocket:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self._q().get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = code
        if self.close_code is None:
            self.close_code = code
        await self._q().put(None)


@dataclass
class WsDial:
    url: str
    origin: str | None
    headers: dict[str, str]


@pytest.fixture
def comfy_ws(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeComfySocket, list[WsDial]]:
    sock = FakeComfySocket()
    dials: list[WsDial] = []

    async def fake_connect(url: str, *, origin: str | None, headers: dict[str, str]) -> Any:
        dials.append(WsDial(url, origin, dict(headers)))
        return sock

    monkeypatch.setattr(comfyui_proxy, "_ws_connect", fake_connect)
    return sock, dials


# ---------------------------------------------------------------------------
# Auth postures


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[None]:
    """Auth enforcement on, with an admin and a client key (the dashboard signs in)."""
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", ALLOWED_ORIGIN)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    auth_mod._require_auth_cache = None
    yield
    auth_mod._require_auth_cache = None


@pytest.fixture
def open_box(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The shipped default: auth off, loopback bind, no keys (conftest pins the rest)."""
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", ALLOWED_ORIGIN)
    auth_mod._require_auth_cache = None


def _sign_in(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"key": ADMIN_KEY})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# P1: admin tier, deny by default


def test_proxy_requires_a_dashboard_session(
    secured: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    resp = client.get("/comfyui/")
    assert resp.status_code == 401
    assert comfy.requests == []

    _sign_in(client)
    resp = client.get("/comfyui/")
    assert resp.status_code == 200
    assert resp.text == "comfyui"
    assert [r.url.raw_path for r in comfy.requests] == [b"/"]


def test_proxy_refuses_a_non_admin_credential(
    secured: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    resp = client.get("/comfyui/", headers={"Authorization": f"Bearer {CLIENT_KEY}"})
    assert resp.status_code == 403
    resp = client.post(
        "/comfyui/api/prompt",
        json={"prompt": {}},
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
    )
    assert resp.status_code == 403
    assert comfy.requests == []


def test_proxy_follows_the_lan_admin_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, app: FastAPI, comfy: FakeComfyUI
) -> None:
    """Auth off, but bound past loopback with an admin key: off-box callers need a session."""
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    auth_mod._require_auth_cache = None

    lan = TestClient(app)  # peer ("testclient", 50000): not loopback
    assert lan.get("/comfyui/").status_code == 401
    assert comfy.requests == []
    _sign_in(lan)
    assert lan.get("/comfyui/").status_code == 200

    console = TestClient(app, client=("127.0.0.1", 50000))
    assert console.get("/comfyui/").status_code == 200


def test_open_box_serves_the_editor_like_its_other_admin_routes(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    resp = client.get("/comfyui/")
    assert resp.status_code == 200
    assert resp.text == "comfyui"  # from ComfyUI, not the dashboard's SPA fallback


def test_editor_routes_are_classified_admin() -> None:
    for method, path in [
        ("GET", "/comfyui"),
        ("GET", "/comfyui/"),
        ("GET", "/comfyui/assets/index.js"),
        ("POST", "/comfyui/api/prompt"),
        ("DELETE", "/comfyui/api/userdata/x.json"),
        ("GET", "/comfyui/ws"),
        ("GET", "/extensions/core/groupNode.js"),
    ]:
        assert classify(method, path) is AuthClass.ADMIN, (method, path)
        rule = match_rule(method, path)
        assert rule is not None and "comfyui" in rule.label, (method, path)


def test_editor_routes_are_not_mcp_tools(app: FastAPI) -> None:
    route_map, _ = build_admin_route_map(app)
    leaked = [p for _verb, p in route_map.values() if p.startswith(("/comfyui", "/extensions"))]
    assert leaked == []


# ---------------------------------------------------------------------------
# HTTP relay


def test_proxy_forwards_http_to_loopback(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    comfy.respond = lambda _req: _reply(
        201, json_body={"nodes": 1}, headers={"cache-control": "no-cache", "x-comfy": "yes"}
    )
    resp = client.get("/comfyui/api/object_info?refresh=1&lang=en")
    assert resp.status_code == 201
    assert resp.json() == {"nodes": 1}
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-comfy"] == "yes"
    (seen,) = comfy.requests
    assert seen.method == "GET"
    assert str(seen.url) == f"http://{UPSTREAM}/api/object_info?refresh=1&lang=en"


def test_prompt_reaches_the_path_the_gpu_gate_watches(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """The gate node matches ``POST /api/prompt`` exactly; the prefix must come off."""
    resp = client.post("/comfyui/api/prompt", json={"prompt": {"1": {}}, "client_id": "c"})
    assert resp.status_code == 200
    (seen,) = comfy.requests
    assert (seen.method, seen.url.raw_path) == ("POST", b"/api/prompt")
    assert seen.body == b'{"prompt":{"1":{}},"client_id":"c"}'
    assert seen.headers["content-type"] == "application/json"


def test_comfyui_base_url_setting_is_the_only_upstream(
    open_box: None, client: TestClient, comfy: FakeComfyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = client.get(
        "/comfyui/x",
        headers={
            "host": "evil.example",
            "x-forwarded-host": "evil.example",
            "forwarded": "host=evil.example",
        },
    )
    assert resp.status_code == 200
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:9999/")
    client.get("/comfyui/y")
    assert [str(r.url) for r in comfy.requests] == [
        f"http://{UPSTREAM}/x",
        "http://127.0.0.1:9999/y",
    ]
    assert [r.headers["host"] for r in comfy.requests] == [UPSTREAM, "127.0.0.1:9999"]


# ---------------------------------------------------------------------------
# P3: hal0's credentials stay in hal0


def test_upstream_never_sees_hal0_credentials(
    secured: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    _sign_in(client)
    resp = client.post(
        "/comfyui/api/upload/image?api_key=" + ADMIN_KEY + "&type=input&subfolder=a%20b&api_key=x",
        content=b"png-bytes",
        headers={
            "authorization": f"Bearer {ADMIN_KEY}",
            "x-api-key": ADMIN_KEY,
            "x-hal0-agent": "hermes",
            "x-hal0-private": "1",
            "x-forwarded-for": "10.9.9.9",
            "x-forwarded-host": "evil.example",
            "x-forwarded-proto": "https",
            "x-real-ip": "10.9.9.9",
            "proxy-authorization": "Basic Zm9vOmJhcg==",
            "connection": "keep-alive, x-secret-hop",
            "keep-alive": "timeout=5",
            "te": "trailers",
            "upgrade": "h2c",
            "content-type": "application/octet-stream",
            "comfy-user": "alice",
            "sec-fetch-site": "same-origin",
            "if-none-match": '"abc"',
            "range": "bytes=0-3",
        },
    )
    assert resp.status_code == 200, resp.text
    (seen,) = comfy.requests
    sent = {k.lower() for k in seen.headers}
    for name in (
        "authorization",
        "cookie",
        "x-api-key",
        "x-hal0-agent",
        "x-hal0-private",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "proxy-authorization",
        "keep-alive",
        "te",
        "upgrade",
        "x-secret-hop",
    ):
        assert name not in sent, name
    assert ADMIN_KEY.encode() not in seen.url.raw_path
    assert seen.url.query == b"type=input&subfolder=a%20b"
    assert seen.body == b"png-bytes"
    # What ComfyUI does need still reaches it.
    assert seen.headers["content-type"] == "application/octet-stream"
    assert seen.headers["comfy-user"] == "alice"
    assert seen.headers["sec-fetch-site"] == "same-origin"
    assert seen.headers["if-none-match"] == '"abc"'
    assert seen.headers["range"] == "bytes=0-3"


def test_response_cookies_and_cors_headers_stay_out_of_hal0s_origin(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    comfy.respond = lambda _req: _reply(
        200,
        b"ok",
        [
            ("set-cookie", "hal0_session=forged; Path=/"),
            ("access-control-allow-origin", "*"),
            ("access-control-allow-credentials", "true"),
            ("connection", "keep-alive, x-hop"),
            ("x-hop", "1"),
            ("keep-alive", "timeout=5"),
            ("vary", "accept"),
            ("vary", "origin"),
        ],
    )
    resp = client.get("/comfyui/")
    assert resp.status_code == 200
    for name in ("set-cookie", "access-control-allow-origin", "access-control-allow-credentials"):
        assert name not in resp.headers, name
    assert "x-hop" not in resp.headers
    assert "keep-alive" not in resp.headers
    assert resp.headers.get_list("vary") == ["accept", "origin"]
    assert "hal0_session" not in client.cookies


def test_compressed_bodies_pass_through_untouched(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    payload = b'{"big": "' + b"x" * 5000 + b'"}'
    packed = gzip.compress(payload)
    comfy.respond = lambda _req: _reply(
        200, packed, {"content-type": "application/json", "content-encoding": "gzip"}
    )
    resp = client.get("/comfyui/api/object_info", headers={"accept-encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["content-length"] == str(len(packed))
    assert resp.content == payload
    assert comfy.requests[0].headers["accept-encoding"] == "gzip"


def test_upstream_gets_identity_encoding_when_the_client_names_none(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    client.get("/comfyui/", headers={"accept-encoding": ""})
    assert comfy.requests[0].headers["accept-encoding"] == "identity"


# ---------------------------------------------------------------------------
# P2 + P5: Origin


def test_host_and_origin_are_rewritten_for_comfyui(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """hal0 validates the browser's Origin, then ComfyUI sees its own Host and Origin."""
    resp = client.post("/comfyui/api/prompt", json={}, headers={"origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200, resp.text  # the fake applies ComfyUI's check
    (seen,) = comfy.requests
    assert seen.headers["host"] == UPSTREAM
    assert seen.headers["origin"] == f"http://{UPSTREAM}"


def test_no_origin_is_invented(open_box: None, client: TestClient, comfy: FakeComfyUI) -> None:
    client.get("/comfyui/")
    assert "origin" not in comfy.requests[0].headers


@pytest.mark.parametrize("posture", ["secured", "open_box"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "GET"])
def test_a_foreign_origin_is_refused(
    posture: str,
    method: str,
    request: pytest.FixtureRequest,
    client: TestClient,
    comfy: FakeComfyUI,
) -> None:
    """At every posture: once hal0 rewrites Origin, ComfyUI's own check can't see it."""
    request.getfixturevalue(posture)
    if posture == "secured":
        _sign_in(client)
    resp = client.request(method, "/comfyui/api/prompt", headers={"origin": FOREIGN_ORIGIN})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.origin_forbidden"
    assert comfy.requests == []


def test_cross_site_fetch_metadata_reaches_comfyui(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """``Sec-Fetch-Site: cross-site`` is forwarded, so ComfyUI's own refusal still applies."""
    resp = client.get("/comfyui/api/view?filename=a.png", headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403
    assert comfy.requests[0].headers["sec-fetch-site"] == "cross-site"


# ---------------------------------------------------------------------------
# P4: fixed upstream, no escape from the prefix


@pytest.mark.parametrize(
    "path",
    [
        "/comfyui/%2e%2e/api/settings",
        "/comfyui/%2E%2E/%2e%2e/etc/passwd",
        "/comfyui/api/%2e%2e%2f%2e%2e%2fapi/settings",
        "/comfyui/api/userdata/..%5c..%5csecret",
        "/comfyui/%2e/api/prompt",
        "/comfyui/api/view%00",
    ],
)
def test_a_path_cannot_escape_the_prefix(
    path: str, open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    resp = client.get(path)
    assert resp.status_code == 400, (path, resp.status_code)
    assert resp.json()["error"]["code"] == "comfyui.editor_path_invalid"
    assert comfy.requests == []


def test_a_raw_dot_segment_is_refused(open_box: None, app: FastAPI, comfy: FakeComfyUI) -> None:
    """A client that sends ``..`` unnormalised (httpx would normalise it) is refused too."""
    status, _ = asyncio.run(_asgi_request(app, "GET", "/comfyui/../api/settings"))
    assert status == 400
    assert comfy.requests == []


def test_extra_slashes_cannot_name_another_host(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    assert client.get("/comfyui//evil.example/x").status_code == 200
    (seen,) = comfy.requests
    assert seen.url.host == "127.0.0.1"
    assert seen.url.port == 8188
    assert seen.url.raw_path == b"/evil.example/x"


def test_encoded_slashes_reach_comfyui_unchanged(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """The editor's userdata calls encode ``/`` inside one path segment."""
    client.get("/comfyui/api/userdata/workflows%2Fmy%20flow.json?overwrite=true")
    assert (
        comfy.requests[0].url.raw_path == b"/api/userdata/workflows%2Fmy%20flow.json?overwrite=true"
    )


# ---------------------------------------------------------------------------
# P7: streaming, limits, failures


def _http_scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8080"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8080),
    }


async def _asgi_request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query: bytes = b"",
) -> tuple[int, bytes]:
    """Drive the app with a raw ASGI request: what a client may send that httpx won't."""
    sent: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not done.is_set():
            done.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = _http_scope(method, path, headers or [])
    scope["query_string"] = query
    await asyncio.wait_for(app(scope, receive, send), 5)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


async def test_a_large_response_streams_instead_of_buffering(
    open_box: None, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first chunk reaches the browser before ComfyUI has produced the second."""
    first_delivered = asyncio.Event()
    chunk = b"a" * 65536

    async def body() -> AsyncIterator[bytes]:
        yield chunk
        await first_delivered.wait()  # a buffering proxy deadlocks here
        yield b"b" * 65536

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=body())

    _use_transport(monkeypatch, _StreamingTransport(handler))
    sent: list[dict[str, Any]] = []
    got_request = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not got_request.is_set():
            got_request.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_delivered.set()

    scope = _http_scope("GET", "/comfyui/api/view", [])
    await asyncio.wait_for(app(scope, receive, send), 5)
    bodies = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert bodies[0]["body"] == chunk
    assert bodies[0]["more_body"] is True
    assert b"".join(m["body"] for m in bodies) == chunk + b"b" * 65536


async def test_an_upload_streams_to_comfyui_instead_of_buffering(
    open_box: None, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ComfyUI gets the first part of an upload before the browser has sent the rest."""
    first_arrived = asyncio.Event()
    received: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        async for part in request.stream:  # type: ignore[union-attr]
            received.append(part)
            first_arrived.set()
        return _reply(200, b"uploaded")

    _use_transport(monkeypatch, _StreamingTransport(handler))
    parts = [b"x" * 1000, b"y" * 1000]
    step = 0

    async def receive() -> dict[str, Any]:
        nonlocal step
        step += 1
        if step == 1:
            return {"type": "http.request", "body": parts[0], "more_body": True}
        if step == 2:
            await first_arrived.wait()  # a buffering proxy deadlocks here
            return {"type": "http.request", "body": parts[1], "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"content-length", b"2000"), (b"content-type", b"application/octet-stream")]
    scope = _http_scope("POST", "/comfyui/api/upload/image", headers)
    await asyncio.wait_for(app(scope, receive, send), 5)
    assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 200
    assert b"".join(received) == parts[0] + parts[1]


def test_an_upload_over_the_limit_is_refused(
    open_box: None, client: TestClient, comfy: FakeComfyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(comfyui_proxy, "MAX_BODY_BYTES", 10)
    resp = client.post("/comfyui/api/upload/image", content=b"x" * 11)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "comfyui.editor_body_too_large"
    # A chunked body declares no length; it is cut off when it passes the limit.
    resp = client.post("/comfyui/api/upload/image", content=iter([b"x" * 6, b"y" * 6]))
    assert resp.status_code == 413
    assert comfy.requests == []
    # At the limit, it goes through.
    assert client.post("/comfyui/api/upload/image", content=b"x" * 10).status_code == 200


@pytest.mark.parametrize("length", ["-2", "1e3", "\u00b2"])
def test_a_bad_content_length_is_refused(
    length: str, open_box: None, app: FastAPI, comfy: FakeComfyUI
) -> None:
    headers = [(b"content-length", length.encode("utf-8"))]
    status, _ = asyncio.run(_asgi_request(app, "POST", "/comfyui/api/prompt", headers=headers))
    assert status == 400
    assert comfy.requests == []


def test_a_query_with_raw_bytes_outside_ascii_is_refused(
    open_box: None, app: FastAPI, comfy: FakeComfyUI
) -> None:
    status, _ = asyncio.run(_asgi_request(app, "GET", "/comfyui/api/view", query=b"f=\xe9.png"))
    assert status == 400
    assert comfy.requests == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (httpx.ConnectError("refused"), 502, "comfyui.editor_unreachable"),
        (httpx.ReadTimeout("slow"), 504, "comfyui.editor_timeout"),
    ],
)
def test_an_unreachable_comfyui_answers_with_an_envelope(
    error: Exception,
    status: int,
    code: str,
    open_box: None,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    _use_transport(monkeypatch, httpx.MockTransport(handler))
    resp = client.get("/comfyui/")
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


# ---------------------------------------------------------------------------
# Redirects


def test_the_bare_prefix_redirects_to_the_editor(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """The editor derives its API base from the path, so it needs the trailing slash."""
    resp = client.get("/comfyui", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/comfyui/"
    assert comfy.requests == []


def test_extension_files_redirect_under_the_prefix(
    open_box: None, client: TestClient, comfy: FakeComfyUI
) -> None:
    """Node help docs give their images a root-absolute ``/extensions/...`` base."""
    resp = client.get("/extensions/my-node/docs/en/cat%20pic.png?v=2", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/comfyui/extensions/my-node/docs/en/cat%20pic.png?v=2"
    resp = client.get("/extensions/%2e%2e/api/settings", follow_redirects=False)
    assert resp.status_code == 400
    assert comfy.requests == []


def test_extension_redirect_needs_the_admin_tier(secured: None, client: TestClient) -> None:
    assert client.get("/extensions/x/y.js", follow_redirects=False).status_code == 401


# ---------------------------------------------------------------------------
# WebSocket


def test_proxy_upgrades_websocket(
    secured: None,
    client: TestClient,
    comfy_ws: tuple[FakeComfySocket, list[WsDial]],
) -> None:
    sock, dials = comfy_ws
    _sign_in(client)
    with client.websocket_connect(
        f"/comfyui/ws?clientId=abc&api_key={ADMIN_KEY}",
        headers={
            "origin": ALLOWED_ORIGIN,
            "authorization": f"Bearer {ADMIN_KEY}",
            "sec-fetch-site": "same-origin",
            "comfy-user": "alice",
            "x-forwarded-for": "10.9.9.9",
        },
    ) as ws:
        assert ws.receive_text() == FakeComfySocket.GREETING
        ws.send_text('{"type": "feature_flags", "data": {}}')
        assert ws.receive_text() == '{"type": "feature_flags", "data": {}}'
        ws.send_bytes(b"\x00\x01preview")
        assert ws.receive_bytes() == b"\x00\x01preview"
    (dial,) = dials
    assert dial.url == f"ws://{UPSTREAM}/ws?clientId=abc"
    assert dial.origin == f"http://{UPSTREAM}"
    assert dial.headers == {"sec-fetch-site": "same-origin", "comfy-user": "alice"}
    assert sock.sent == ['{"type": "feature_flags", "data": {}}', b"\x00\x01preview"]


@pytest.mark.parametrize("credential", [None, f"Bearer {CLIENT_KEY}"])
def test_websocket_needs_the_admin_tier(
    credential: str | None,
    secured: None,
    client: TestClient,
    comfy_ws: tuple[FakeComfySocket, list[WsDial]],
) -> None:
    headers = {"origin": ALLOWED_ORIGIN}
    if credential:
        headers["authorization"] = credential
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/comfyui/ws", headers=headers),
    ):
        pass
    assert refused.value.code == 4403
    assert comfy_ws[1] == []


@pytest.mark.parametrize("posture", ["secured", "open_box"])
def test_websocket_with_a_foreign_origin_is_refused_before_accept(
    posture: str,
    request: pytest.FixtureRequest,
    client: TestClient,
    comfy_ws: tuple[FakeComfySocket, list[WsDial]],
) -> None:
    """Cross-site websocket hijacking: refused at the handshake, ComfyUI never dialled."""
    request.getfixturevalue(posture)
    if posture == "secured":
        _sign_in(client)
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/comfyui/ws", headers={"origin": FOREIGN_ORIGIN}),
    ):
        pass
    assert refused.value.code == 4403
    assert comfy_ws[1] == []


def test_websocket_closes_when_comfyui_closes(
    open_box: None,
    client: TestClient,
    comfy_ws: tuple[FakeComfySocket, list[WsDial]],
) -> None:
    sock, _ = comfy_ws
    with client.websocket_connect("/comfyui/ws", headers={"origin": ALLOWED_ORIGIN}) as ws:
        assert ws.receive_text() == FakeComfySocket.GREETING
        ws.send_text("close please")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1000
    assert sock.closed_with is not None  # the proxy's side of the upstream socket is shut too


def test_websocket_closes_comfyui_when_the_browser_leaves(
    open_box: None,
    client: TestClient,
    comfy_ws: tuple[FakeComfySocket, list[WsDial]],
) -> None:
    sock, _ = comfy_ws
    with client.websocket_connect("/comfyui/ws", headers={"origin": ALLOWED_ORIGIN}) as ws:
        assert ws.receive_text() == FakeComfySocket.GREETING
        ws.close(1001)
    assert sock.closed_with is not None


def test_websocket_to_an_unreachable_comfyui_is_denied_with_502(
    open_box: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(url: str, *, origin: str | None, headers: dict[str, str]) -> Any:
        raise ConnectionRefusedError("nothing on 8188")

    monkeypatch.setattr(comfyui_proxy, "_ws_connect", refused)
    with (
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect("/comfyui/ws", headers={"origin": ALLOWED_ORIGIN}),
    ):
        pass
    assert denied.value.status_code == 502


def _ws_proxy_kwargs() -> dict[str, Any]:
    params = inspect.signature(ws_client_connect).parameters
    return {"proxy": None} if "proxy" in params else {}


@contextlib.contextmanager
def _comfy_like_ws_server() -> Iterator[tuple[int, list[dict[str, str]]]]:
    """A real websocket server on loopback that refuses what ComfyUI's check refuses."""
    seen: list[dict[str, str]] = []
    ready = threading.Event()
    state: dict[str, Any] = {}

    def process_request(connection: Any, request: Any) -> Any:
        headers = {k.lower(): v for k, v in request.headers.raw_items()}
        seen.append(headers)
        if _comfyui_refuses(headers):
            return connection.respond(HTTPStatus.FORBIDDEN, "")
        return None

    async def handler(socket: Any) -> None:
        await socket.send(FakeComfySocket.GREETING)
        async for message in socket:
            await socket.send(message)

    async def main() -> None:
        state["loop"] = asyncio.get_running_loop()
        state["stop"] = asyncio.Event()
        async with ws_serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
            state["port"] = next(iter(server.sockets)).getsockname()[1]
            ready.set()
            await state["stop"].wait()

    thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        yield state["port"], seen
    finally:
        state["loop"].call_soon_threadsafe(state["stop"].set)
        thread.join(5)


def test_websocket_handshake_satisfies_comfyuis_origin_check(
    open_box: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the wire, ComfyUI sees its own Host and Origin, and none of hal0's credentials."""
    with _comfy_like_ws_server() as (port, seen):
        monkeypatch.setenv("COMFYUI_BASE_URL", f"http://127.0.0.1:{port}")
        with client.websocket_connect(
            "/comfyui/ws?clientId=wire",
            headers={"origin": ALLOWED_ORIGIN, "cookie": "hal0_session=abc; other=1"},
        ) as ws:
            assert ws.receive_text() == FakeComfySocket.GREETING
            ws.send_text("ping")
            assert ws.receive_text() == "ping"

        (handshake,) = seen
        assert handshake["host"] == f"127.0.0.1:{port}"
        assert handshake["origin"] == f"http://127.0.0.1:{port}"
        assert "cookie" not in handshake
        assert "authorization" not in handshake

        # Control: the browser's own Origin, sent straight to it, is refused.
        async def direct() -> None:
            async with ws_client_connect(
                f"ws://127.0.0.1:{port}/ws", origin=ALLOWED_ORIGIN, **_ws_proxy_kwargs()
            ):
                pass

        with pytest.raises(InvalidStatus):
            asyncio.run(direct())


# ---------------------------------------------------------------------------
# P8: /api/config/urls


def _urls(client: TestClient, **headers: str) -> dict[str, Any]:
    with patch(
        "hal0.api.routes.config._openwebui_is_active", new_callable=AsyncMock, return_value=False
    ):
        resp = client.get("/api/config/urls", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_config_urls_report_proxy_and_link(client: TestClient) -> None:
    direct = _urls(client, host="hal0-test.lan:8080")
    assert direct["comfyui_proxy"] is True
    assert direct["comfyui"] == "/comfyui/"
    behind = _urls(client, **{"x-forwarded-host": "hal0.example.com", "x-forwarded-proto": "https"})
    assert behind["comfyui_proxy"] is True
    assert behind["comfyui"] == "/comfyui/"


def test_public_url_override_wins(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_PUBLIC_URL", "https://comfyui.example.com/")
    body = _urls(client, host="hal0-test.lan:8080")
    assert body["comfyui"] == "https://comfyui.example.com"
    assert body["comfyui_proxy"] is True
