"""Tests for POST /api/models/check-updates (HF re-pull refresh).

The check compares each installed row's recorded content sha256
(``metadata["sha256"]``, written by ``_register_pulled`` on every pull)
against the LFS sha256 HF currently advertises for the same
repo/filename — the ``X-Linked-ETag`` on the ``resolve/main`` endpoint.

httpx is mocked the same way as the inspect tests
(tests/api/test_models_routes.py): the route builds its own AsyncClient,
so the class is swapped for a factory that injects a MockTransport.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import models as models_route

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
def updates_app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app with the update-check cache cleared (cold cache per test)."""
    models_route._UPDATE_CHECK_CACHE.clear()
    return create_app()


@pytest.fixture
def updates_client(updates_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(updates_app) as c:
        yield c


def _register(
    client: TestClient,
    tmp_home: str,
    model_id: str,
    *,
    hf_repo: str = "",
    hf_filename: str = "",
    sha256: str | None = None,
) -> None:
    """Seed a registry row shaped like a completed pull."""
    fpath = Path(tmp_home) / f"{model_id}.gguf"
    fpath.write_bytes(b"\x00" * 8)
    body: dict[str, Any] = {"id": model_id, "path": str(fpath)}
    if hf_repo:
        body["hf_repo"] = hf_repo
    if hf_filename:
        body["hf_filename"] = hf_filename
    if sha256:
        body["metadata"] = {"sha256": sha256}
    r = client.post("/api/models", json=body)
    assert r.status_code == 201, r.text


def _head_handler(latest_sha: str | None, *, status: int = 302, calls: list[str] | None = None):
    """MockTransport handler answering HEAD resolve/main like huggingface.co.

    HF serves the LFS sha256 as ``X-Linked-ETag`` on the (unfollowed) 302
    hop to the CDN; ``latest_sha=None`` omits the header (non-LFS file).
    """

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        headers = {}
        if latest_sha is not None:
            headers["x-linked-etag"] = f'"{latest_sha}"'
        return httpx.Response(status, headers=headers)

    return handler


def _patch_httpx_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr("hal0.api.routes.models.httpx.AsyncClient", factory)


def _row(body: dict[str, Any], model_id: str) -> dict[str, Any]:
    rows = {m["id"]: m for m in body["models"]}
    assert model_id in rows, f"{model_id} missing from {sorted(rows)}"
    return rows[model_id]


def test_update_available_when_upstream_sha_differs(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        updates_client,
        tmp_hal0_home,
        "qwen",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B))

    body = updates_client.post("/api/models/check-updates").json()
    assert body["cached"] is False
    assert body["count"] == 1
    assert body["updates_available"] == 1
    row = _row(body, "qwen")
    assert row["installed_sha256"] == SHA_A
    assert row["latest_sha256"] == SHA_B
    assert row["update_available"] is True
    assert row["error"] is None


def test_no_update_when_sha_matches(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        updates_client,
        tmp_hal0_home,
        "qwen",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_A))

    body = updates_client.post("/api/models/check-updates").json()
    assert body["updates_available"] == 0
    assert _row(body, "qwen")["update_available"] is False


def test_rows_without_hf_coords_are_skipped(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand-registered files (no hf_repo/hf_filename) never hit HF."""
    _register(updates_client, tmp_hal0_home, "local-only", sha256=SHA_A)
    calls: list[str] = []
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B, calls=calls))

    body = updates_client.post("/api/models/check-updates").json()
    assert body["count"] == 0
    assert body["models"] == []
    assert calls == []


def test_missing_local_sha_is_reported_but_never_flagged(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-hash registrations can't be compared — no false-positive badge."""
    _register(updates_client, tmp_hal0_home, "old", hf_repo="org/repo", hf_filename="q.gguf")
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B))

    body = updates_client.post("/api/models/check-updates").json()
    assert body["updates_available"] == 0
    row = _row(body, "old")
    assert row["installed_sha256"] is None
    assert row["latest_sha256"] == SHA_B
    assert row["update_available"] is False


def test_second_call_is_served_from_cache(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        updates_client,
        tmp_hal0_home,
        "qwen",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    calls: list[str] = []
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B, calls=calls))

    first = updates_client.post("/api/models/check-updates").json()
    second = updates_client.post("/api/models/check-updates").json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1
    assert second["updates_available"] == 1


def test_force_bypasses_cache(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        updates_client,
        tmp_hal0_home,
        "qwen",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    calls: list[str] = []
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B, calls=calls))

    updates_client.post("/api/models/check-updates")
    updates_client.post("/api/models/check-updates", json={"force": True})
    assert len(calls) == 2


def test_cached_result_clears_after_repull_lands_new_sha(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed sha is read fresh each call — a completed re-pull flips
    the row to up-to-date within the HF cache TTL, no invalidation needed."""
    _register(
        updates_client,
        tmp_hal0_home,
        "qwen",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    _patch_httpx_transport(monkeypatch, _head_handler(SHA_B))

    first = updates_client.post("/api/models/check-updates").json()
    assert first["updates_available"] == 1

    # Simulate the re-pull's _register_pulled: registry row now carries the
    # upstream sha.
    r = updates_client.put("/api/models/qwen", json={"metadata": {"sha256": SHA_B}})
    assert r.status_code == 200, r.text

    second = updates_client.post("/api/models/check-updates").json()
    assert second["cached"] is True
    assert second["updates_available"] == 0
    assert _row(second, "qwen")["update_available"] is False


def test_hf_error_lands_on_the_row_not_the_response(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        updates_client,
        tmp_hal0_home,
        "gone",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    _patch_httpx_transport(monkeypatch, _head_handler(None, status=404))

    r = updates_client.post("/api/models/check-updates")
    assert r.status_code == 200
    row = _row(r.json(), "gone")
    assert row["update_available"] is False
    assert row["latest_sha256"] is None
    assert "404" in (row["error"] or "") or "no longer exists" in (row["error"] or "")


def test_non_lfs_file_reports_no_upstream_sha(
    updates_client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200/302 without an X-Linked-ETag sha (non-LFS file) is unknowable."""
    _register(
        updates_client,
        tmp_hal0_home,
        "tiny",
        hf_repo="org/repo",
        hf_filename="q.gguf",
        sha256=SHA_A,
    )
    _patch_httpx_transport(monkeypatch, _head_handler(None, status=200))

    row = _row(updates_client.post("/api/models/check-updates").json(), "tiny")
    assert row["update_available"] is False
    assert row["latest_sha256"] is None
    assert row["error"] == "no LFS sha256 advertised"
