"""Tests for the Hindsight admin surface — /api/memory/engine + /api/memory/banks/*.

The admin router forwards an allowlisted slice of the Hindsight REST API
(loopback :9177) through hal0-api so the dashboard can manage banks, browse
the graph, and drive recall/reflect/operations. These tests stub Hindsight
with an httpx.MockTransport behind the real HindsightRestClient.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_admin_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory_admin
from hal0.memory.hindsight_client import HindsightRestClient

# ── harness ────────────────────────────────────────────────────────────────────


class _Recorder:
    """Captures upstream requests; serves canned responses by (method, path)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.fail_connect = False

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_connect:
            raise httpx.ConnectError("connection refused", request=request)
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
        return httpx.Response(200, json={"echo": request.url.path})


class _HindsightStubProvider:
    """Duck-typed provider exposing the hindsight client like HindsightProvider."""

    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


class _OtherEngineProvider:
    """A provider with no hindsight client (e.g. pgvector fallback)."""


def _build_app(provider: Any) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_admin.router, prefix="/api/memory", tags=["memory"])
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


# ── gating ─────────────────────────────────────────────────────────────────────


def test_banks_503_when_memory_disabled() -> None:
    app = _build_app(None)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "memory.unavailable"


def test_banks_501_when_engine_not_hindsight() -> None:
    app = _build_app(_OtherEngineProvider())
    with TestClient(app) as c:
        r = c.get("/api/memory/banks")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "memory.engine_unsupported"


def test_bank_id_with_invalid_chars_400(client: TestClient) -> None:
    r = client.get("/api/memory/banks/bad..id/stats")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "memory.invalid_bank"


# ── forwarding: reads ──────────────────────────────────────────────────────────


def test_banks_list_forwards_and_returns_payload(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "shared", "fact_count": 2}]}
    )
    r = client.get("/api/memory/banks")
    assert r.status_code == 200
    assert r.json()["banks"][0]["bank_id"] == "shared"


def test_graph_forwards_query_params(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks/shared/graph", 200, {"nodes": [], "edges": []})
    r = client.get(
        "/api/memory/banks/shared/graph", params={"type": "world", "limit": 50, "q": "x"}
    )
    assert r.status_code == 200
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/graph"
    assert fwd["params"] == {"type": "world", "limit": "50", "q": "x"}


def test_entities_graph_min_count_param(client: TestClient, recorder: _Recorder) -> None:
    r = client.get("/api/memory/banks/shared/entities/graph", params={"min_count": 2})
    assert r.status_code == 200
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/entities/graph"
    assert fwd["params"] == {"min_count": "2"}


def test_timeseries_maps_to_memories_timeseries(client: TestClient, recorder: _Recorder) -> None:
    r = client.get("/api/memory/banks/shared/stats/timeseries", params={"period": "7d"})
    assert r.status_code == 200
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/stats/memories-timeseries"
    assert fwd["params"] == {"period": "7d"}


def test_memories_list_maps_paths_and_params(client: TestClient, recorder: _Recorder) -> None:
    r = client.get(
        "/api/memory/banks/shared/memories",
        params={"type": "world", "q": "alpha", "limit": 10, "offset": 5},
    )
    assert r.status_code == 200
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/memories/list"
    assert fwd["params"] == {"type": "world", "q": "alpha", "limit": "10", "offset": "5"}


# ── forwarding: writes ─────────────────────────────────────────────────────────


def test_recall_post_passes_body_through(client: TestClient, recorder: _Recorder) -> None:
    body = {"query": "what changed", "budget": "high", "include": {"entities": {}}}
    r = client.post("/api/memory/banks/shared/recall", json=body)
    assert r.status_code == 200
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/memories/recall"
    assert '"budget": "high"' in fwd["body"] or '"budget":"high"' in fwd["body"]


def test_reflect_post_forwards(client: TestClient, recorder: _Recorder) -> None:
    r = client.post("/api/memory/banks/shared/reflect", json={"query": "who am i"})
    assert r.status_code == 200
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/reflect"


def test_bank_create_put_and_delete_forward(client: TestClient, recorder: _Recorder) -> None:
    r = client.put("/api/memory/banks/scratch", json={"retain_extraction_mode": "concise"})
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "PUT"
    assert recorder.requests[-1]["path"] == "/v1/default/banks/scratch"

    r = client.delete("/api/memory/banks/scratch")
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "DELETE"


def test_operation_retry_and_consolidate_forward(client: TestClient, recorder: _Recorder) -> None:
    r = client.post("/api/memory/banks/shared/operations/op-1/retry")
    assert r.status_code == 200
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/operations/op-1/retry"

    r = client.post("/api/memory/banks/shared/consolidate", json={})
    assert r.status_code == 200
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/consolidate"


def test_mental_model_refresh_forwards(client: TestClient, recorder: _Recorder) -> None:
    r = client.post("/api/memory/banks/shared/mental-models/mm-1/refresh")
    assert r.status_code == 200
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/mental-models/mm-1/refresh"


def test_document_delete_and_reprocess_forward(client: TestClient, recorder: _Recorder) -> None:
    r = client.delete("/api/memory/banks/shared/documents/doc-1")
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "DELETE"
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/documents/doc-1"

    r = client.post("/api/memory/banks/shared/documents/doc-1/reprocess")
    assert r.status_code == 200
    assert recorder.requests[-1]["path"] == "/v1/default/banks/shared/documents/doc-1/reprocess"


# ── upstream error mapping ─────────────────────────────────────────────────────


def test_upstream_4xx_maps_to_same_status_with_engine_error_code(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.respond("GET", "/v1/default/banks/ghost/stats", 404, {"detail": "bank not found"})
    r = client.get("/api/memory/banks/ghost/stats")
    assert r.status_code == 404
    payload = r.json()["error"]
    assert payload["code"] == "memory.engine_error"
    assert "bank not found" in str(payload["details"])


def test_upstream_5xx_maps_to_502(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 500, {"detail": "boom"})
    r = client.get("/api/memory/banks")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "memory.engine_error"


def test_upstream_unreachable_maps_to_503(client: TestClient, recorder: _Recorder) -> None:
    recorder.fail_connect = True
    r = client.get("/api/memory/banks")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "memory.engine_unreachable"


# ── GET /api/memory/engine (fail-soft aggregator) ──────────────────────────────


def test_engine_aggregator_reports_version_features_and_bank_count(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.respond(
        "GET",
        "/version",
        200,
        {"api_version": "0.7.2", "features": {"observations": True, "mcp": True}},
    )
    recorder.respond(
        "GET",
        "/v1/default/banks",
        200,
        {"banks": [{"bank_id": "shared"}, {"bank_id": "private__hermes"}]},
    )
    r = client.get("/api/memory/engine")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["engine"] == "hindsight"
    assert body["reachable"] is True
    assert body["version"] == "0.7.2"
    assert body["features"]["observations"] is True
    assert body["banks_total"] == 2


def test_engine_aggregator_fail_soft_when_unreachable(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.fail_connect = True
    r = client.get("/api/memory/engine")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["reachable"] is False
    assert body["version"] is None


def test_engine_aggregator_when_memory_disabled() -> None:
    app = _build_app(None)
    with TestClient(app) as c:
        r = c.get("/api/memory/engine")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["reachable"] is False
    assert body["engine"] is None


# ── mounted on the real app ────────────────────────────────────────────────────


def test_engine_route_mounted_on_create_app(tmp_hal0_home: str) -> None:
    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/memory/engine")
    # Any non-404 means the router is mounted; payload contract covered above.
    assert r.status_code == 200
    assert "enabled" in r.json()
