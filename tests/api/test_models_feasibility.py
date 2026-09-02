"""Tests for POST /api/models/feasibility — batch GTT preflight.

Advisory-only: never 404s, never blocks. Unknown model ids or a missing
GPU sample (no ``hardware_stats`` wired) yield ``verdict: "unknown"`` rows
rather than an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── isolated app fixture (mirrors test_models_crud.py:~90-110) ──────────────


@pytest.fixture
def crud_app(tmp_hal0_home: str) -> FastAPI:
    extra_root = Path(tmp_hal0_home) / "crud-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{extra_root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture
def crud_client(crud_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(crud_app) as c:
        yield c


@pytest.fixture
def crud_models_root(tmp_hal0_home: str) -> Path:
    return Path(tmp_hal0_home) / "crud-models"


def _create_minimal_model(client: TestClient, models_root: Path) -> str:
    """Register a minimal model and return its id."""
    fpath = models_root / "feas-test.gguf"
    fpath.write_bytes(b"\x00" * 64)
    mid = "feas-test"
    r = client.post("/api/models", json={"id": mid, "path": str(fpath)})
    assert r.status_code == 201, r.text
    return mid


# ── tests ─────────────────────────────────────────────────────────────────


def test_feasibility_unknown_without_gpu_sample(crud_client: TestClient) -> None:
    """No hardware_stats on app.state → every verdict is 'unknown', 200 always."""
    r = crud_client.post("/api/models/feasibility", json={"models": [{"model_id": "nope"}]})
    assert r.status_code == 200, r.text
    row = r.json()["results"][0]
    assert row["model_id"] == "nope"
    assert row["verdict"] == "unknown"


def test_feasibility_ctx_override_raises_need(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    mid = _create_minimal_model(crud_client, crud_models_root)

    class _Gpu:  # duck-type GPUMemorySample
        gtt_free_mb = 69632.0
        gtt_total_mb = 98304.0
        is_uma = True

    class _Stats:
        def gpu_sample(self) -> _Gpu:
            return _Gpu()

    crud_client.app.state.hardware_stats = _Stats()

    small = crud_client.post(
        "/api/models/feasibility",
        json={"models": [{"model_id": mid, "ctx": 8192}]},
    ).json()["results"][0]
    big = crud_client.post(
        "/api/models/feasibility",
        json={"models": [{"model_id": mid, "ctx": 200000}]},
    ).json()["results"][0]

    assert big["needed_mb"] > small["needed_mb"]
    assert small["verdict"] in {"fits", "tight", "exceeds", "exceeds_total"}


def test_feasibility_route_ordering_get_is_405_not_model_lookup(
    crud_client: TestClient,
) -> None:
    """GET /api/models/feasibility must 405 — never resolve as model_id='feasibility'."""
    r = crud_client.get("/api/models/feasibility")
    assert r.status_code == 405, r.text


def test_feasibility_empty_batch_returns_empty_results(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/feasibility", json={"models": []})
    assert r.status_code == 200, r.text
    assert r.json() == {"results": []}


def test_feasibility_row_error_degrades_to_unknown_without_500ing_batch(
    crud_client: TestClient,
    crud_models_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row whose downstream computation raises (corrupt/unexpected stored
    data — ``model_dump``/``estimate_file_size_kv_mb``/
    ``gtt_feasibility_verdict`` are all reachable failure points past a
    successful ``registry.get``) degrades to 'unknown' for just that row —
    the rest of the batch still computes and the request never 500s.

    Simulates the downstream failure by monkeypatching
    ``estimate_file_size_kv_mb`` (imported into ``routes.models``) to raise
    only for the "bad" model id, leaving the real implementation in place
    for every other row — this exercises the route's own try/except rather
    than a registry-internals quirk.
    """
    from hal0.api.routes import models as models_route

    good_id = _create_minimal_model(crud_client, crud_models_root)
    bad_id = "feas-bad"
    fpath = crud_models_root / "feas-bad.gguf"
    fpath.write_bytes(b"\x00" * 32)
    r = crud_client.post("/api/models", json={"id": bad_id, "path": str(fpath)})
    assert r.status_code == 201, r.text

    class _Gpu:  # duck-type GPUMemorySample
        gtt_free_mb = 69632.0
        gtt_total_mb = 98304.0
        is_uma = True

    class _Stats:
        def gpu_sample(self) -> _Gpu:
            return _Gpu()

    crud_client.app.state.hardware_stats = _Stats()

    real_estimate = models_route.estimate_file_size_kv_mb

    def _boom(model_mb: float, ctx_meta: dict | None, **kw: object) -> float:
        if isinstance(ctx_meta, dict) and ctx_meta.get("id") == bad_id:
            raise ValueError("simulated corrupt row")
        return real_estimate(model_mb, ctx_meta, **kw)

    monkeypatch.setattr(models_route, "estimate_file_size_kv_mb", _boom)

    resp = crud_client.post(
        "/api/models/feasibility",
        json={"models": [{"model_id": good_id}, {"model_id": bad_id}]},
    )
    assert resp.status_code == 200, resp.text
    rows = {row["model_id"]: row for row in resp.json()["results"]}
    assert rows[bad_id]["verdict"] == "unknown"
    assert rows[bad_id]["needed_mb"] == 0.0
    assert rows[good_id]["verdict"] in {"fits", "tight", "exceeds", "exceeds_total"}
