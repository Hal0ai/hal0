"""CLI→API requests to loopback never go through the proxy environment (#2503).

With ``HTTP_PROXY`` (or ``ALL_PROXY``) set and ``NO_PROXY`` not covering
loopback, httpx's default ``trust_env=True`` sends a request for
``http://127.0.0.1:8080`` — bearer header included — to the proxy in
cleartext. These tests run a real fake-proxy listener and a real fake API
listener: every loopback call must land on the API and never on the proxy,
and a remote target must still go through the proxy.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from hal0.cli import _shared, setup_command, setup_install
from hal0.install.orchestrate import Selections

KEY = "TESTKEY-2503"
REMOTE = "http://hal0-remote.invalid:8080"
_BODY = {"model_ids": [], "slots": [], "api": "http://dashboard.example/"}

_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


class _Listener:
    """A real HTTP listener on an ephemeral loopback port that records every
    request line and its Authorization header, and answers 200 with JSON."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None]] = []
        seen = self.seen

        class _Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                seen.append((self.requestline, self.headers.get("Authorization")))
                body = json.dumps(_BODY).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_POST = do_HEAD = _answer

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def api() -> Iterator[_Listener]:
    listener = _Listener()
    yield listener
    listener.close()


@pytest.fixture(params=["HTTP_PROXY", "ALL_PROXY"])
def proxy(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Listener]:
    """A fake proxy wired in through ``HTTP_PROXY`` or ``ALL_PROXY``, with no
    ``NO_PROXY`` — the environment the issue reproduces in."""
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    listener = _Listener()
    monkeypatch.setenv(request.param, listener.url)
    yield listener
    listener.close()


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", KEY)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)


def _assert_direct(api: _Listener, proxy: _Listener, count: int = 1) -> None:
    assert proxy.seen == [], f"loopback request reached the proxy: {proxy.seen}"
    assert len(api.seen) == count
    assert all(auth == f"Bearer {KEY}" for _, auth in api.seen)


def _assert_proxied(proxy: _Listener, count: int = 1) -> None:
    assert len(proxy.seen) == count
    assert all(line.split()[1].startswith(REMOTE) for line, _ in proxy.seen)


# --------------------------------------------------------------------------- helper


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8080", "http://localhost:8080/api/x", "http://[::1]:8080/", None],
)
def test_client_kwargs_loopback_ignores_environment(url: str | None) -> None:
    kwargs = _shared._client_kwargs(url)
    assert kwargs["trust_env"] is False
    # The CA context still honours SSL_CERT_FILE/SSL_CERT_DIR for an
    # https loopback API.
    from hal0.http_client import shared_ssl_context

    assert kwargs["verify"] is shared_ssl_context(trust_env=True)


@pytest.mark.parametrize(
    "url", [REMOTE, "http://10.1.2.3:8080", "http://evil@127.0.0.1:8080", "", "not a url"]
)
def test_client_kwargs_remote_keeps_environment(url: str) -> None:
    assert _shared._client_kwargs(url) == {}


def test_auth_client_mounts(proxy: _Listener) -> None:
    """Inspect the transports httpx picked: loopback has no proxy mount,
    a remote target routes through the env proxy."""
    with _shared.auth_client(target="http://127.0.0.1:8080") as client:
        transport = client._transport_for_url(httpx.URL("http://127.0.0.1:8080/api/x"))
        assert transport is client._transport
        assert client._mounts == {}
    with _shared.auth_client(target=REMOTE) as client:
        transport = client._transport_for_url(httpx.URL(f"{REMOTE}/api/x"))
        assert transport is not client._transport


# --------------------------------------------------------------------------- live sockets


def test_auth_client_loopback_bypasses_proxy(api: _Listener, proxy: _Listener) -> None:
    with _shared.auth_client(target=api.url, timeout=5.0) as client:
        assert client.get(f"{api.url}/api/x").status_code == 200
    _assert_direct(api, proxy)


def test_auth_client_remote_uses_proxy(proxy: _Listener) -> None:
    with _shared.auth_client(target=REMOTE, timeout=5.0) as client:
        assert client.get(f"{REMOTE}/api/x").status_code == 200
    _assert_proxied(proxy)
    # The exported key is still sent to an explicit remote target (#2499) —
    # through the operator's proxy, which is their configured route.
    assert proxy.seen[0][1] == f"Bearer {KEY}"


def test_api_request_loopback_bypasses_proxy(api: _Listener, proxy: _Listener) -> None:
    assert _shared.api_get("/api/x", base=api.url, timeout=5.0) == _BODY
    assert _shared.api_post("/api/y", base=api.url, json={"a": 1}, timeout=5.0) == _BODY
    _assert_direct(api, proxy, count=2)


def test_api_request_remote_uses_proxy(proxy: _Listener) -> None:
    assert _shared.api_get("/api/x", base=REMOTE, timeout=5.0) == _BODY
    _assert_proxied(proxy)


def test_api_get_bytes_loopback_bypasses_proxy(api: _Listener, proxy: _Listener) -> None:
    content, _ = _shared.api_get_bytes("/api/bundle", base=api.url, timeout=5.0)
    assert json.loads(content) == _BODY
    _assert_direct(api, proxy)


def test_api_get_bytes_remote_uses_proxy(proxy: _Listener) -> None:
    _shared.api_get_bytes("/api/bundle", base=REMOTE, timeout=5.0)
    _assert_proxied(proxy)


def test_api_stream_loopback_bypasses_proxy(api: _Listener, proxy: _Listener) -> None:
    with _shared.api_stream("GET", "/api/logs", base=api.url, timeout=5.0) as resp:
        resp.read()
        assert resp.status_code == 200
    _assert_direct(api, proxy)


def test_api_stream_remote_uses_proxy(proxy: _Listener) -> None:
    with _shared.api_stream("GET", "/api/logs", base=REMOTE, timeout=5.0) as resp:
        resp.read()
    _assert_proxied(proxy)


def test_api_unreachable_probe_bypasses_proxy(api: _Listener, proxy: _Listener) -> None:
    assert _shared._api_unreachable(api.url) is False
    assert proxy.seen == []
    assert [line.split()[0] for line, _ in api.seen] == ["HEAD"]


def test_setup_api_reachable_bypasses_proxy(
    api: _Listener, proxy: _Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_API_URL", api.url)
    assert setup_command._api_reachable(timeout=5.0) is True
    _assert_direct(api, proxy)


def test_setup_api_reachable_remote_uses_proxy(
    proxy: _Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_API_URL", REMOTE)
    assert setup_command._api_reachable(timeout=5.0) is True
    _assert_proxied(proxy)


async def test_setup_apply_via_api_bypasses_proxy(
    api: _Listener, proxy: _Listener, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Covers both setup_install clients: the apply POST and the
    ``_dashboard_url`` GET that follows it."""
    monkeypatch.setenv("HAL0_API_URL", api.url)
    await setup_install._apply_via_api(Selections(storage_dir="", slots=[], extensions={}))
    _assert_direct(api, proxy, count=2)
    assert "Dashboard: http://dashboard.example/" in capsys.readouterr().out


async def test_setup_apply_via_api_remote_uses_proxy(
    proxy: _Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_API_URL", REMOTE)
    await setup_install._apply_via_api(Selections(storage_dir="", slots=[], extensions={}))
    _assert_proxied(proxy, count=2)
