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
        # FIFO: each queued response answers one upstream request, in order
        # (the 0.9.2 async export is a multi-request exchange, #2155).
        self.queue: list[httpx.Response] = []

    def respond_next(self, response: httpx.Response) -> None:
        self.queue.append(response)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "host": request.url.host,
                "path": request.url.path,
                "params": dict(request.url.params),
                "content_type": request.headers.get("content-type", ""),
                "body": request.content,
            }
        )
        return self.queue.pop(0) if self.queue else httpx.Response(200, json={})


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
    # 0.8.4 engines answer the sync GET directly — no async export round-trip.
    assert len(recorder.requests) == 1


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


# ── GET export on hindsight-api 0.9.2: sync GET is a 410 tombstone (#2155) ───

_ZIP = b"PK\x03\x04async-export-zip"


def _gone() -> httpx.Response:
    return httpx.Response(
        410,
        json={
            "detail": "Synchronous document export has been removed ... Submit an async "
            "export via POST /v1/default/banks/shared/document-transfer/export"
        },
    )


@pytest.fixture
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_admin, "_EXPORT_POLL_INTERVAL_S", 0.0, raising=False)


def _queue_async_export(
    recorder: _Recorder, *, download_url: str, extra_meta: dict[str, Any] | None = None
) -> None:
    recorder.respond_next(_gone())
    recorder.respond_next(httpx.Response(202, json={"operation_id": "exp-1", "status": "pending"}))
    recorder.respond_next(httpx.Response(200, json={"operation_id": "exp-1", "status": "pending"}))
    recorder.respond_next(
        httpx.Response(
            200,
            json={
                "operation_id": "exp-1",
                "status": "completed",
                "result_metadata": {"download_url": download_url, **(extra_meta or {})},
            },
        )
    )
    recorder.respond_next(
        httpx.Response(200, content=_ZIP, headers={"content-type": "application/zip"})
    )


def test_export_410_falls_back_to_async_export(
    client: TestClient, recorder: _Recorder, fast_poll: None
) -> None:
    _queue_async_export(
        recorder, download_url="/v1/default/files/download/banks/shared/exports/u1/transfer.zip"
    )
    r = client.get(
        "/api/memory/banks/shared/document-transfer", params={"include_observations": "false"}
    )
    assert r.status_code == 200, r.text
    assert r.content == _ZIP
    assert r.headers["content-type"].startswith("application/zip")
    calls = [(q["method"], q["path"]) for q in recorder.requests]
    assert calls == [
        ("GET", "/v1/default/banks/shared/document-transfer"),
        ("POST", "/v1/default/banks/shared/document-transfer/export"),
        ("GET", "/v1/default/banks/shared/operations/exp-1"),
        ("GET", "/v1/default/banks/shared/operations/exp-1"),
        ("GET", "/v1/default/files/download/banks/shared/exports/u1/transfer.zip"),
    ]
    assert recorder.requests[1]["params"] == {"include_observations": "false"}
    assert {q["host"] for q in recorder.requests} == {"127.0.0.1"}


def test_export_async_accepts_absolute_same_origin_download_url(
    client: TestClient, recorder: _Recorder, fast_poll: None
) -> None:
    _queue_async_export(
        recorder, download_url="http://127.0.0.1:9177/v1/default/files/download/banks/shared/k.zip"
    )
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 200, r.text
    assert r.content == _ZIP
    assert recorder.requests[-1]["path"] == "/v1/default/files/download/banks/shared/k.zip"


def test_export_async_foreign_host_download_url_uses_engine_storage_key(
    client: TestClient, recorder: _Recorder, fast_poll: None
) -> None:
    # An object-store backend hands out a pre-signed URL on another host;
    # hal0-api must never follow it — it fetches the same archive from the
    # engine's own files/download endpoint by storage_key instead.
    _queue_async_export(
        recorder,
        download_url="https://bucket.example.invalid/banks/shared/k.zip?sig=x",
        extra_meta={"storage_key": "banks/shared/exports/u2/transfer.zip"},
    )
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 200, r.text
    assert r.content == _ZIP
    assert recorder.requests[-1]["path"] == (
        "/v1/default/files/download/banks/shared/exports/u2/transfer.zip"
    )
    assert {q["host"] for q in recorder.requests} == {"127.0.0.1"}


@pytest.mark.parametrize(
    "download_url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:9999/v1/default/files/download/banks/shared/k.zip",
        "//evil.example.invalid/v1/default/files/download/banks/shared/k.zip",
    ],
)
def test_export_async_foreign_origin_without_storage_key_is_refused(
    client: TestClient, recorder: _Recorder, fast_poll: None, download_url: str
) -> None:
    _queue_async_export(recorder, download_url=download_url)
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "memory.engine_error"
    # Nothing past the operation poll was fetched.
    assert len(recorder.requests) == 4


def test_export_async_operation_failed_surfaces_error(
    client: TestClient, recorder: _Recorder, fast_poll: None
) -> None:
    recorder.respond_next(_gone())
    recorder.respond_next(httpx.Response(202, json={"operation_id": "exp-1"}))
    recorder.respond_next(
        httpx.Response(
            200, json={"operation_id": "exp-1", "status": "failed", "error_message": "disk full"}
        )
    )
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 502
    body = r.json()["error"]
    assert body["code"] == "memory.engine_error"
    assert body["details"]["status"] == "failed"
    assert body["details"]["error_message"] == "disk full"
    assert len(recorder.requests) == 3


def test_export_async_operation_poll_times_out(
    client: TestClient, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_admin, "_EXPORT_POLL_INTERVAL_S", 0.0, raising=False)
    monkeypatch.setattr(memory_admin, "_EXPORT_POLL_TIMEOUT_S", 0.0, raising=False)
    recorder.respond_next(_gone())
    recorder.respond_next(httpx.Response(202, json={"operation_id": "exp-1"}))
    recorder.respond_next(httpx.Response(200, json={"operation_id": "exp-1", "status": "pending"}))
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 504
    body = r.json()["error"]
    assert body["code"] == "memory.engine_error"
    assert body["details"]["operation_id"] == "exp-1"
    assert body["details"]["status"] == "timed_out"


def test_export_async_submit_error_passes_through(
    client: TestClient, recorder: _Recorder, fast_poll: None
) -> None:
    recorder.respond_next(_gone())
    recorder.respond_next(httpx.Response(404, json={"detail": "Document export API is disabled."}))
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "memory.engine_error"


def test_export_non_410_error_does_not_try_async(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond_next(httpx.Response(500, json={"detail": "boom"}))
    r = client.get("/api/memory/banks/shared/document-transfer")
    assert r.status_code == 502
    assert len(recorder.requests) == 1


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
