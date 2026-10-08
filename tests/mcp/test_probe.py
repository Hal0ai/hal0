"""Tests for :mod:`hal0.mcp.probe` — the generic user-server MCP prober.

Stubs ``probe._open`` rather than hitting the network (except the #2304
redirect/proxy tests, which need urllib's real handlers); each fake response is
the JSON-RPC frame a real streamable-http MCP server would return for
``initialize``/``tools/list``.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import URLError

import pytest

from hal0.mcp import installed, probe


def _record(**overrides: object) -> installed.InstalledServer:
    defaults: dict[str, object] = {
        "id": "github",
        "name": "github",
        "spec": "https://github.example.com/manifest.json",
        "transport": "streamable-http",
        "url": "https://github.example.com/mcp",
    }
    defaults.update(overrides)
    return installed.InstalledServer(**defaults)


class _FakeResponse:
    def __init__(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_probe_returns_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _FakeResponse({"jsonrpc": "2.0", "id": "1", "result": {}}),
        _FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "result": {"tools": [{"name": "search_repositories"}, {"name": "get_file"}]},
            }
        ),
    ]

    def fake_open(req: Any, *, timeout: float) -> _FakeResponse:
        return responses.pop(0)

    monkeypatch.setattr(probe, "_open", fake_open)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is True
    assert result["tools"] == ["search_repositories", "get_file"]
    assert result["error"] is None


def test_probe_reports_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(req: Any, *, timeout: float) -> None:
        raise URLError("connection refused")

    monkeypatch.setattr(probe, "_open", fake_open)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is False
    assert "connection refused" in result["error"]
    assert result["tools"] == []


def test_probe_reports_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _FakeResponse({"jsonrpc": "2.0", "id": "1", "result": {}}),
        _FakeResponse({"jsonrpc": "2.0", "id": "2", "error": {"message": "unauthorized"}}),
    ]

    def fake_open(req: Any, *, timeout: float) -> _FakeResponse:
        return responses.pop(0)

    monkeypatch.setattr(probe, "_open", fake_open)
    result = probe.probe_installed_server_sync(_record())
    assert result["ok"] is False
    assert result["error"] == "unauthorized"


def test_probe_rejects_stdio_transport() -> None:
    record = _record(transport="stdio", url="", spec="npm:foo")
    result = probe.probe_installed_server_sync(record)
    assert result["ok"] is False
    assert "stdio" in result["error"]


def test_probe_rejects_missing_url() -> None:
    record = _record(url="")
    result = probe.probe_installed_server_sync(record)
    assert result["ok"] is False
    assert "url" in result["error"]


def test_build_headers_resolves_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "sekret")
    record = _record(
        env={"MCP_WORKSPACE": "/tmp/ws"},
        secrets={"Authorization": "GITHUB_MCP_TOKEN"},
    )
    headers = probe.build_headers(record)
    assert headers["Authorization"] == "sekret"
    assert headers["MCP_WORKSPACE"] == "/tmp/ws"
    assert headers["X-hal0-Agent"] == "hermes"


def test_build_headers_omits_unresolved_secret() -> None:
    record = _record(secrets={"Authorization": "NOT_SET_ANYWHERE"})
    headers = probe.build_headers(record)
    assert "Authorization" not in headers


# ── #2304: the probe must not carry header values past the TLS gate ─────────
#
# These use real loopback HTTP servers and the probe's real urllib opener —
# the bypasses below live in urllib's redirect and proxy handlers, which a
# stubbed opener would hide.

_Responder = Callable[[bytes], tuple[int, dict[str, str], bytes]]


@pytest.fixture
def http_server() -> Iterator[Callable[[str, _Responder], tuple[str, list[dict[str, Any]]]]]:
    """Start recording HTTP servers; yields ``serve(host, respond) -> (base_url, hits)``."""
    servers: list[ThreadingHTTPServer] = []

    def serve(host: str, respond: _Responder) -> tuple[str, list[dict[str, Any]]]:
        hits: list[dict[str, Any]] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                hits.append({"path": self.path, "headers": dict(self.headers)})
                status, headers, payload = respond(body)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                return None

        try:
            server = ThreadingHTTPServer((host, 0), _Handler)
        except OSError as exc:  # e.g. 127.0.0.2 not routed to lo on this OS
            pytest.skip(f"cannot bind {host}: {exc}")
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://{host}:{server.server_address[1]}", hits

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def _jsonrpc_ok(body: bytes) -> tuple[int, dict[str, str], bytes]:
    method = json.loads(body or b"{}").get("method")
    result: dict[str, Any] = {"tools": [{"name": "search"}]} if method == "tools/list" else {}
    payload = json.dumps({"jsonrpc": "2.0", "id": "1", "result": result}).encode()
    return 200, {"Content-Type": "application/json"}, payload


def test_probe_refuses_redirect_and_sends_nothing_onward(
    monkeypatch: pytest.MonkeyPatch, http_server: Any
) -> None:
    """urllib follows a 30x and copies the headers; a passing URL could then
    hand the secret to a plaintext non-loopback host. The probe refuses."""
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    base, hits = http_server(
        "127.0.0.1", lambda body: (302, {"Location": "http://192.0.2.10/mcp"}, b"")
    )
    connects: list[Any] = []
    real_create_connection = socket.create_connection

    def recording_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        connects.append(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", recording_create_connection)
    record = _record(url=f"{base}/mcp", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"})

    result = probe.probe_installed_server_sync(record, timeout=2)

    assert result["ok"] is False
    assert "redirect" in result["error"]
    assert "http://192.0.2.10/mcp" in result["error"]
    assert len(hits) == 1
    assert all(addr[0] != "192.0.2.10" for addr in connects)


def test_probe_bypasses_env_proxy_for_loopback(
    monkeypatch: pytest.MonkeyPatch, http_server: Any
) -> None:
    """A loopback URL that NO_PROXY misses must still go direct, never via a proxy."""
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    proxy_base, proxy_hits = http_server("127.0.0.1", _jsonrpc_ok)
    target_base, target_hits = http_server("127.0.0.2", _jsonrpc_ok)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy_base)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    # urllib caches its global opener (and the env proxies it read) on first
    # use; drop it so a plain urlopen() would see the proxy set above.
    monkeypatch.setattr(urllib.request, "_opener", None)
    record = _record(url=f"{target_base}/mcp", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"})

    result = probe.probe_installed_server_sync(record, timeout=2)

    assert result == {"ok": True, "tools": ["search"], "error": None}
    assert proxy_hits == []
    assert len(target_hits) == 2
    assert target_hits[0]["headers"]["Authorization"] == "shh-secret-value"
