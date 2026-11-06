"""Tests for the memory showcase surface — engine passthrough, lanes, agents, models.

Mirrors the harness in test_memory_admin_routes.py: an httpx.MockTransport
stands in for Hindsight, mounted behind a real HindsightRestClient so auth
headers / base-url plumbing stay exercised.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_lanes_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory_lanes
from hal0.memory.hindsight_client import HindsightRestClient


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": request.content.decode() if request.content else "",
            }
        )
        key = (request.method, request.url.path)
        if key in self.responses:
            return self.responses[key]
        if request.url.path == "/v1/default/banks":
            return httpx.Response(200, json={"banks": []})
        return httpx.Response(200, json={"echo": request.url.path})


class _HindsightStubProvider:
    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


def _build_app(provider: Any, cfg: Any | None = None) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_lanes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = provider
    return app


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def client(recorder: _Recorder) -> Iterator[TestClient]:
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177")
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    app = _build_app(_HindsightStubProvider(rest))
    with TestClient(app) as c:
        yield c


# ── engine passthrough ───────────────────────────────────────────────────


def test_engine_banks_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "shared"}]})
    r = client.get("/api/memory/engine/v1/default/banks")
    assert r.status_code == 200
    assert r.json() == {"banks": [{"bank_id": "shared"}]}


def test_engine_bank_subpath_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET", "/v1/default/banks/shared/stats", 200, {"bank_id": "shared", "total_nodes": 3}
    )
    r = client.get("/api/memory/engine/v1/default/banks/shared/stats")
    assert r.status_code == 200
    assert r.json()["total_nodes"] == 3


def test_engine_version_and_health_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/version", 200, {"version": "1.2.0"})
    recorder.respond("GET", "/health", 200, {"status": "ok"})
    assert client.get("/api/memory/engine/version").json() == {"version": "1.2.0"}
    assert client.get("/api/memory/engine/health").json() == {"status": "ok"}


def test_engine_post_body_forwarded(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "POST", "/v1/default/banks/shared/reflect", 200, {"ok": True}
    )
    r = client.post("/api/memory/engine/v1/default/banks/shared/reflect", json={"q": "x"})
    assert r.status_code == 200
    import json as _json

    assert _json.loads(recorder.requests[-1]["body"]) == {"q": "x"}


def test_engine_disallowed_path_404(client: TestClient) -> None:
    r = client.get("/api/memory/engine/v1/default/admin/shutdown")
    assert r.status_code == 404


def test_engine_path_traversal_404(client: TestClient) -> None:
    r = client.get("/api/memory/engine/v1/default/banks/../../etc/passwd")
    assert r.status_code in (404, 400)


def test_engine_unreachable_503(recorder: _Recorder) -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="http://127.0.0.1:9177")
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    app = _build_app(_HindsightStubProvider(rest))
    with TestClient(app) as c:
        r = c.get("/api/memory/engine/v1/default/banks")
    assert r.status_code == 503


def test_engine_no_provider_503() -> None:
    app = _build_app(None)
    with TestClient(app) as c:
        r = c.get("/api/memory/engine/version")
    assert r.status_code == 503


# ── lanes / agents / models ──────────────────────────────────────────────


def test_lanes_basic_shape(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "shared"}]}
    )
    recorder.respond(
        "GET",
        "/v1/default/banks/shared/operations",
        200,
        {"operations": [{"status": "pending", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    body = r.json()
    assert "decision" in body and "llm" in body and "banks" in body
    assert body["decision"]["reachable"] is False
    bank_row = next(b for b in body["banks"] if b["bank_id"] == "shared")
    assert bank_row["pending"] == 1
    assert body["llm"]["per_op"]["reflect"]["max_concurrent"] >= 1


def test_lanes_bank_operations_error_included(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "broken"}]}
    )
    recorder.respond("GET", "/v1/default/banks/broken/operations", 500, {"detail": "boom"})
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    body = r.json()
    bank_row = next(b for b in body["banks"] if b["bank_id"] == "broken")
    assert "error" in bank_row and isinstance(bank_row["error"], str)


def test_lanes_npu_absent_is_null(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    assert r.json()["npu"] is None


def test_agents_filters_coding_agent_banks(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET",
        "/v1/default/banks",
        200,
        {"banks": [{"bank_id": "shared"}, {"bank_id": "coding-agent::repo1"}]},
    )
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["banks"] == ["coding-agent::repo1"]
    assert body["install"]["harnesses"] == [
        "claude-code",
        "codex",
        "opencode",
        "pi",
        "cursor-cli",
        "copilot-cli",
        "qwen-code",
    ]
    assert "hindsight-coding-agents@0.8.5" in body["install"]["command"]
    assert body["console_url"] is None


def test_agents_console_url_disabled_by_default(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    assert r.json()["console_url"] is None


def test_agents_console_url_enabled(
    client: TestClient, recorder: _Recorder, monkeypatch
) -> None:
    from hal0.api.routes import memory_lanes as mod

    class _Console:
        enabled = True
        port = 9999
        url = None

    class _MemCfg:
        public_url = None
        console = _Console()

    class _Cfg:
        memory = _MemCfg()

    monkeypatch.setattr(mod, "load_hal0_config", lambda: _Cfg())
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["console_url"] is not None
    assert body["console_url"].endswith(":9999")


def test_models_shape(client: TestClient) -> None:
    r = client.get("/api/memory/models")
    assert r.status_code == 200
    body = r.json()
    assert body["embeddings"]["provider"] == "local"
    assert body["decision"]["model"] == "hs-decide-1-flash"
    assert body["decision"]["reachable"] is False
