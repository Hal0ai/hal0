"""Loopback agent/memory requests never go through a proxy (#2506).

The agent memory commands, the agent shim readiness probe and the other
urllib callers of this box's loopback API used ``urllib.request.urlopen``,
whose default opener honours ``HTTP_PROXY``. With a proxy set and ``NO_PROXY``
not covering loopback the request went to the proxy instead of the API.

These tests run a real fake API and a real fake proxy listener, set
``HTTP_PROXY`` to the proxy and assert the request reaches the API and the
proxy sees nothing.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision
from hal0.cli import agent_commands, agent_shim
from hal0.mcp import admin
from hal0.memory import engine_upgrade

_CARD = {
    "id": "card-1",
    "metadata": {"agent_id": "hermes", "display_name": "Hermes", "roles": ["chat"]},
}


class _Listener:
    """A real HTTP listener recording ``(method, path, X-hal0-Agent)``."""

    def __init__(self, *, memory: bool) -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                outer.seen.append((self.command, self.path, self.headers.get("X-hal0-Agent")))
                if memory and self.path.endswith("/api/memory/search"):
                    body: Any = {"items": [_CARD]}
                elif memory and self.path.endswith("/api/memory/delete"):
                    body = {"deleted": 1}
                else:
                    body = {"status": "ok"}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(raw)

            do_GET = do_POST = do_HEAD = _handle

            def log_message(self, *a: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(params=["HTTP_PROXY", "http_proxy"])
def proxied(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """(api, proxy): HTTP_PROXY points at a live fake proxy, NO_PROXY unset."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.delenv("REQUEST_METHOD", raising=False)
    # urlopen caches its default opener, built from the env at first use.
    monkeypatch.setattr(urllib.request, "_opener", None)
    api = _Listener(memory=True)
    proxy = _Listener(memory=True)
    monkeypatch.setenv(request.param, proxy.url)
    monkeypatch.setenv("HAL0_API_URL", api.url)
    try:
        yield api, proxy
    finally:
        api.close()
        proxy.close()


def test_agent_peers_reaches_the_api_not_the_proxy(proxied: Any, capsys) -> None:
    api, proxy = proxied
    agent_commands.agent_peers()

    assert ("POST", "/api/memory/search", "hal0-cli") in api.seen
    assert proxy.seen == []
    assert "unreachable" not in capsys.readouterr().out.lower()


def test_agent_uninstall_memory_reaches_the_api_not_the_proxy(proxied: Any) -> None:
    api, proxy = proxied
    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome != "unreachable"
    paths = [p for _, p, _ in api.seen]
    assert paths[0] == "/api/memory/search"
    assert "/api/memory/delete" in paths
    assert proxy.seen == []


def test_agent_shim_readiness_probe_bypasses_the_proxy(proxied: Any) -> None:
    api, proxy = proxied
    cfg = agent_shim.AgentConfig(
        agent_id="hermes",
        agent_type="hermes",
        home=Path("/x"),
        venv=Path("/y"),
        host="127.0.0.1",
        port=api.port,
    )

    assert agent_shim._is_ready(cfg) is True
    assert api.seen and proxy.seen == []


def test_bootstrap_probes_bypass_the_proxy(proxied: Any) -> None:
    api, proxy = proxied

    assert hermes_provision._http_get(f"{api.url}/health") == 200
    status, body = hermes_provision._http_status_json(f"{api.url}/api/status")
    assert (status, body) == (200, {"status": "ok"})
    assert len(api.seen) == 2 and proxy.seen == []


def test_engine_upgrade_probe_bypasses_the_proxy(proxied: Any) -> None:
    api, proxy = proxied

    assert engine_upgrade._http_get_json(f"{api.url}/version") == {"status": "ok"}
    assert proxy.seen == []


def test_mcp_call_rest_bypasses_the_proxy(proxied: Any) -> None:
    """``_call_rest`` forwards the caller's bearer to the loopback REST API;
    httpx reads the proxy environment unless told not to."""
    api, proxy = proxied

    result = asyncio.run(
        admin._call_rest(
            base_url=api.url,
            bearer="not-a-real-key",
            method="GET",
            url="/api/status",
            payload=None,
        )
    )

    assert result == {"status": "ok"}
    assert [(m, p) for m, p, _ in api.seen] == [("GET", "/api/status")]
    assert proxy.seen == []
