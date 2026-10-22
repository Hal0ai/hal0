"""End to end: a fake Hermes API server, hal0 boot reconcile, /v1 relay.

The fake serves the Hermes shapes hal0 relies on (default profile at ``/v1``,
the ``coder`` profile under ``/p/coder/v1``, bearer-checked). hal0 boots with
``HERMES_HOME`` pointing at a tmp home whose ``config.yaml`` and ``.env`` files
reference the fake, so the real boot phase registers the upstreams.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents import hermes_api
from hal0.api import create_app

DEFAULT_KEY = "default-key-0123456789abcdef0123456789"
CODER_KEY = "coder-key-0123456789abcdef0123456789ab"
_ROUTE = "hal0.api.routes.services"
_CHUNK = (
    'data: {"id":"c1","object":"chat.completion.chunk",'
    '"choices":[{"index":0,"delta":{"content":"%s"}}]}\n\n'
)
SSE_BODY = "".join(_CHUNK % c for c in "abc") + "data: [DONE]\n\n"


class FakeHermes(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.received: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    server: FakeHermes

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj).encode())

    def _authed(self, key: str) -> bool:
        if self.headers.get("Authorization") == f"Bearer {key}":
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def _scope(self) -> tuple[str, str] | None:
        """``(key, model_id)`` for the path's profile, or ``None`` (404 sent)."""
        path = self.path.split("?", 1)[0]
        if path.startswith("/p/coder/"):
            return CODER_KEY, "coder"
        if path.startswith("/v1/"):
            return DEFAULT_KEY, "hermes-agent"
        self._json(404, {"error": "not found"})
        return None

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/p/coder/health"):
            self._json(200, {"status": "ok"})
        elif path.startswith("/p/") and path.endswith("/health"):
            self._json(404, {"error": "no such profile"})
        elif path.endswith("/v1/models"):
            scope = self._scope()
            if scope and self._authed(scope[0]):
                self._json(200, {"object": "list", "data": [{"id": scope[1], "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if not self.path.split("?", 1)[0].endswith("/v1/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        scope = self._scope()
        if scope is None or not self._authed(scope[0]):
            return
        self.server.received.append(json.loads(raw))
        self._send(200, SSE_BODY.encode(), "text/event-stream")


@pytest.fixture
def fake_hermes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHermes]:
    server = FakeHermes()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    home = tmp_path / "hermes-home"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "profiles" / "parked").mkdir()  # no /p/parked/health -> not served
    host, port = server.server_address[:2]
    (home / "config.yaml").write_text(
        f"gateway:\n  platforms:\n    api_server:\n      host: {host}\n      port: {port}\n"
    )
    (home / ".env").write_text(f"API_SERVER_KEY={DEFAULT_KEY}\n")
    (home / "profiles" / "coder" / ".env").write_text(f"API_SERVER_KEY={CODER_KEY}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for profile in ("default", "coder"):
            monkeypatch.delenv(hermes_api.env_key_name(profile), raising=False)


@pytest.fixture
def hermes_client(
    fake_hermes: FakeHermes, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Boot the app only after ``HERMES_HOME`` points at the fake."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_hermes_profiles_relay_end_to_end(
    hermes_client: TestClient, fake_hermes: FakeHermes
) -> None:
    models = hermes_client.get("/v1/models")
    assert models.status_code == 200, models.text
    ids = {m["id"] for m in models.json()["data"]}
    assert {"hermes-agent", "coder"} <= ids
    assert "parked" not in ids

    sent = {"model": "coder", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    with hermes_client.stream("POST", "/v1/chat/completions", json=sent) as resp:
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    assert body == SSE_BODY

    assert len(fake_hermes.received) == 1
    received = fake_hermes.received[0]
    assert set(received) == {"model", "messages", "stream"}
    assert received["model"] == "coder"
    assert received["messages"] == sent["messages"]


def test_services_page_reports_wired_agents(hermes_client: TestClient) -> None:
    down = {"active_state": "inactive", "sub_state": "dead", "unit_file_state": "disabled"}
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                f"{_ROUTE}._probe_comfyui",
                return_value=(False, "unreachable", None, None),
            )
        )
        stack.enter_context(patch(f"{_ROUTE}._probe_hermes", return_value=(False, "down")))
        stack.enter_context(patch(f"{_ROUTE}._probe_openwebui", return_value=(False, "down")))
        stack.enter_context(patch(f"{_ROUTE}.svc_systemd.unit_state", return_value=dict(down)))
        stack.enter_context(patch(f"{_ROUTE}.svc_systemd.unit_is_active", return_value=False))
        stack.enter_context(
            patch(
                f"{_ROUTE}.mdns.status",
                return_value={
                    "available": False,
                    "hostname": "t.local",
                    "base_advertised": False,
                    "advertised": [],
                },
            )
        )
        r = hermes_client.get("/api/services")
    assert r.status_code == 200, r.text
    by_id = {s["id"]: s for s in r.json()["services"]}
    assert by_id["openwebui"]["wired"]["agents"] == ["coder", "hermes-agent"]
