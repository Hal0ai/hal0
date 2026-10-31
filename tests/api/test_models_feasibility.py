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


def test_feasibility_non_list_models_is_400(crud_client: TestClient) -> None:
    """``models`` present but not a list is a client error, not a silent no-op."""
    r = crud_client.post("/api/models/feasibility", json={"models": "not-a-list"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "models.feasibility_body_invalid"
    assert "list" in r.json()["error"]["message"]


def test_feasibility_missing_models_key_returns_empty_results(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/feasibility", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"results": []}


def test_feasibility_batch_soft_capped_at_256(crud_client: TestClient) -> None:
    """More than 256 items is not an error — only the first 256 are processed."""
    items = [{"model_id": f"m{i}"} for i in range(300)]
    r = crud_client.post("/api/models/feasibility", json={"models": items})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 256
    assert results[0]["model_id"] == "m0"
    assert results[-1]["model_id"] == "m255"


def test_feasibility_non_dict_item_degrades_in_place(crud_client: TestClient) -> None:
    """A non-dict entry degrades to an 'unknown' row in place rather than being
    dropped — response row count always equals request row count (capped)."""
    good_id = "whatever"
    r = crud_client.post(
        "/api/models/feasibility",
        json={"models": [{"model_id": good_id}, 42, {"model_id": "other"}]},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 3
    middle = results[1]
    assert middle["model_id"] == ""
    assert middle["verdict"] == "unknown"
    assert middle["needed_mb"] == 0.0


# ── ctx resolution: request > profile > native > fallback (ADR-0029 §6) ─────
#
# A model row holds no context tune, so the window a pick is tested at comes
# from the request's ``ctx``, else from the named profile (resolved by the same
# launch resolver a slot uses), else from the model's native metadata.


class _Gpu:  # duck-type GPUMemorySample
    gtt_free_mb = 69632.0
    gtt_total_mb = 98304.0
    is_uma = True


class _Stats:
    def gpu_sample(self) -> _Gpu:
        return _Gpu()


@pytest.fixture
def ctx_client(crud_client: TestClient, crud_models_root: Path, monkeypatch: pytest.MonkeyPatch):
    """A client with a GPU sample, a model with a 131072-token native window, a
    model with no window metadata, and a stub profile catalog."""
    from hal0.config.schema import ProfileConfig
    from hal0.errors import NotFound

    crud_client.app.state.hardware_stats = _Stats()
    for mid, metadata in (("native-big", {"context_length": 131072}), ("native-none", {})):
        fpath = crud_models_root / f"{mid}.gguf"
        fpath.write_bytes(b"\x00" * 64)
        r = crud_client.post(
            "/api/models", json={"id": mid, "path": str(fpath), "metadata": metadata}
        )
        assert r.status_code == 201, r.text

    catalog = {
        "with-ctx": ProfileConfig(flags="-fa on", ctx_size=65536),
        "no-ctx": ProfileConfig(flags="-fa on"),
    }

    def _resolve(name: str) -> ProfileConfig:
        try:
            return catalog[name]
        except KeyError:
            raise NotFound(f"profile {name!r} not found", code="profile.not_found") from None

    monkeypatch.setattr("hal0.providers.container._resolve_profile", _resolve)
    return crud_client


def _feasibility_row(client: TestClient, **row: object) -> dict:
    r = client.post("/api/models/feasibility", json={"models": [row]})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def test_feasibility_request_ctx_is_used_as_given(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big", ctx=16384)
    assert (row["ctx"], row["ctx_source"]) == (16384, "request")


def test_feasibility_profile_ctx_size_is_the_window(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big", profile="with-ctx")
    assert (row["ctx"], row["ctx_source"]) == (65536, "profile")


def test_feasibility_profile_without_ctx_size_uses_the_capped_native_window(
    ctx_client: TestClient,
) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big", profile="no-ctx")
    assert (row["ctx"], row["ctx_source"]) == (32768, "native")


def test_feasibility_no_ctx_no_profile_uses_the_native_window(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big")
    assert (row["ctx"], row["ctx_source"]) == (32768, "native")


def test_feasibility_no_window_anywhere_falls_back_to_8192(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-none")
    assert (row["ctx"], row["ctx_source"]) == (8192, "fallback")


def test_feasibility_request_ctx_wins_over_profile(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big", profile="with-ctx", ctx=4096)
    assert (row["ctx"], row["ctx_source"]) == (4096, "request")


def test_feasibility_unknown_profile_degrades_the_row_to_unknown(ctx_client: TestClient) -> None:
    row = _feasibility_row(ctx_client, model_id="native-big", profile="no-such-profile")
    assert row["verdict"] == "unknown"
    assert row["ctx"] is None
    assert row["ctx_source"] is None
    assert row["needed_mb"] == 0.0


def test_feasibility_bigger_profile_window_needs_more_memory(ctx_client: TestClient) -> None:
    small = _feasibility_row(ctx_client, model_id="native-big", ctx=4096)
    big = _feasibility_row(ctx_client, model_id="native-big", profile="with-ctx")
    assert big["needed_mb"] > small["needed_mb"]


def test_feasibility_unknown_model_row_reports_null_ctx(crud_client: TestClient) -> None:
    row = _feasibility_row(crud_client, model_id="nope", ctx=8192)
    assert row["verdict"] == "unknown"
    assert row["ctx"] is None
    assert row["ctx_source"] is None


def test_feasibility_slot_row_uses_slot_tune_window(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """A row naming a ``slot`` is sized at that slot's [tune] window, source ``slot``."""
    from unittest.mock import patch

    from hal0.config.schema import ProfileConfig

    mid = _create_minimal_model(crud_client, crud_models_root)

    class _Sm:
        async def iter_configs(self):
            return [{"name": "chat", "type": "llm", "profile": "p", "tune": {"ctx_size": 4096}}]

    crud_client.app.state.slot_manager = _Sm()
    with patch(
        "hal0.providers.container._resolve_profile", return_value=ProfileConfig(ctx_size=65536)
    ):
        row = crud_client.post(
            "/api/models/feasibility", json={"models": [{"model_id": mid, "slot": "chat"}]}
        ).json()["results"][0]
        plain = crud_client.post(
            "/api/models/feasibility", json={"models": [{"model_id": mid}]}
        ).json()["results"][0]
    assert (row["ctx"], row["ctx_source"]) == (4096, "slot")
    assert plain["ctx_source"] != "slot"
