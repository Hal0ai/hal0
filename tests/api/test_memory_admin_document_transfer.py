"""Tests for ``/api/memory/banks/{bank}/document-transfer`` (GET export ZIP,
POST multipart import) — the hindsight-api>=0.8.0 cross-bank transfer
surface ``hal0 memory migrate unify`` drives.

These two routes move raw bytes (a ZIP export, a multipart upload) instead
of JSON, so they're hand-rolled rather than table-driven like the rest of
``memory_admin.py`` — see that module's docstring above the routes for why.
Same MockTransport harness as ``test_memory_admin_routes.py``, extended
with a raw-bytes responder and multipart capture.
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


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.next_response: httpx.Response | None = None

    def respond_next(self, response: httpx.Response) -> None:
        self.next_response = response

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "content_type": request.headers.get("content-type", ""),
                "body": request.content,
            }
        )
        resp = self.next_response or httpx.Response(200, json={})
        self.next_response = None
        return resp


class _HindsightStubProvider:
    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


def _build_app(provider: Any) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    # No app.state.audit — record_action() no-ops gracefully when absent
    # (see hal0/api/_audit.py), which is what these unit tests want.
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


# ── GET export ───────────────────────────────────────────────────────────────


def test_export_streams_zip_bytes(client: TestClient, recorder: _Recorder) -> None:
    zip_bytes = b"PK\x03\x04fake-zip-content"
    recorder.respond_next(
        httpx.Response(200, content=zip_bytes, headers={"content-type": "application/zip"})
    )
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 200
    assert r.content == zip_bytes
    assert r.headers["content-type"].startswith("application/zip")
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/shared/document-transfer"
    assert fwd["params"] == {"include_observations": "true"}


def test_export_forwards_include_observations_false(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.respond_next(
        httpx.Response(200, content=b"", headers={"content-type": "application/zip"})
    )
    r = client.get(
        "/api/memory/banks/shared/document-transfer", params={"include_observations": "false"}
    )
    assert r.status_code == 200
    assert recorder.requests[-1]["params"] == {"include_observations": "false"}


def test_export_404_when_feature_disabled_surfaces_upstream_error(
    client: TestClient, recorder: _Recorder
) -> None:
    recorder.respond_next(httpx.Response(404, json={"detail": "document export API disabled"}))
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "memory.engine_error"


def test_export_invalid_bank_id_400(client: TestClient) -> None:
    r = client.get("/api/memory/banks/bad..id/document-transfer")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "memory.invalid_bank"


# ── POST import ──────────────────────────────────────────────────────────────


def test_import_forwards_multipart_and_on_conflict(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond_next(httpx.Response(202, json={"operation_id": "op-1", "status": "queued"}))
    r = client.post(
        "/api/memory/banks/target/document-transfer",
        params={"on_conflict": "replace"},
        files={"file": ("transfer.zip", b"PK\x03\x04fake", "application/zip")},
    )
    assert r.status_code == 200
    assert r.json() == {"operation_id": "op-1", "status": "queued"}
    fwd = recorder.requests[-1]
    assert fwd["path"] == "/v1/default/banks/target/document-transfer"
    assert fwd["params"] == {"on_conflict": "replace"}
    assert fwd["content_type"].startswith("multipart/form-data")
    assert b"fake" in fwd["body"]


def test_import_defaults_on_conflict_to_skip(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond_next(httpx.Response(202, json={"operation_id": "op-2"}))
    r = client.post(
        "/api/memory/banks/target/document-transfer",
        files={"file": ("transfer.zip", b"x", "application/zip")},
    )
    assert r.status_code == 200
    assert recorder.requests[-1]["params"] == {"on_conflict": "skip"}


def test_import_rejects_invalid_on_conflict(client: TestClient) -> None:
    r = client.post(
        "/api/memory/banks/target/document-transfer",
        params={"on_conflict": "yolo"},
        files={"file": ("transfer.zip", b"x", "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "memory.invalid_query"


def test_import_rejects_missing_file_field(client: TestClient) -> None:
    r = client.post("/api/memory/banks/target/document-transfer", data={"not_a_file": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "memory.invalid_body"
