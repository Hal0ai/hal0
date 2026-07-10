"""/api/models/updates — HF update-check list + apply (update-all) routes."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

import hal0.api.routes.model_updates as mu
from hal0.registry.updates import clear_check_cache

LOCAL_SHA = hashlib.sha256(b"old").hexdigest()
REMOTE_SHA = hashlib.sha256(b"new").hexdigest()


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_check_cache()
    yield
    clear_check_cache()


def _register(client, mid: str, *, hf: bool = True) -> None:
    body: dict[str, Any] = {
        "id": mid,
        "name": mid,
        "path": f"/tmp/{mid}/model.gguf",
        "metadata": {"sha256": LOCAL_SHA},
    }
    if hf:
        body["hf_repo"] = f"org/{mid}"
        body["hf_filename"] = "model.gguf"
    res = client.post("/api/models", json=body)
    assert res.status_code == 201


def _stub_check(monkeypatch, statuses: list[dict[str, Any]]):
    calls: list[dict[str, Any]] = []

    async def fake_check(models, *, refresh=False, hf_token=None, **kw):
        calls.append({"models": [m.id for m in models], "refresh": refresh})
        return statuses

    monkeypatch.setattr(mu, "check_for_updates", fake_check)
    return calls


def _status_row(mid: str, *, available: bool) -> dict[str, Any]:
    return {
        "model_id": mid,
        "hf_repo": f"org/{mid}",
        "hf_filename": "model.gguf",
        "local_sha256": LOCAL_SHA,
        "remote_sha256": REMOTE_SHA if available else LOCAL_SHA,
        "status": "update_available" if available else "up_to_date",
        "update_available": available,
        "checked_at": 1.0,
        "error": None,
    }


# ── GET /api/models/updates ───────────────────────────────────────────────────


def test_list_updates_route_not_shadowed_by_model_id(isolated_client, monkeypatch):
    """The literal /updates path must win over GET /api/models/{model_id}."""
    _stub_check(monkeypatch, [])
    res = isolated_client.get("/api/models/updates")
    assert res.status_code == 200
    assert res.json() == {"updates": [], "available": [], "count": 0, "available_count": 0}


def test_list_updates_reports_available(isolated_client, monkeypatch):
    _register(isolated_client, "m1")
    _register(isolated_client, "m2")
    _stub_check(
        monkeypatch,
        [_status_row("m1", available=True), _status_row("m2", available=False)],
    )
    res = isolated_client.get("/api/models/updates")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] == ["m1"]
    assert body["available_count"] == 1
    assert body["count"] == 2
    by_id = {r["model_id"]: r for r in body["updates"]}
    assert by_id["m1"]["update_available"] is True
    assert by_id["m2"]["status"] == "up_to_date"


def test_refresh_clears_cache_and_emits_event(isolated_app_client, monkeypatch):
    _, client = isolated_app_client
    _register(client, "m1")
    _stub_check(monkeypatch, [_status_row("m1", available=True)])
    cleared: list[bool] = []
    monkeypatch.setattr(mu, "clear_check_cache", lambda: cleared.append(True))

    res = client.get("/api/models/updates?refresh=true")
    assert res.status_code == 200
    assert cleared == [True]

    events = client.get("/api/events", params={"type": "model.updates_available"})
    rows = events.json()["events"]
    assert len(rows) == 1
    assert rows[0]["data"]["model_ids"] == ["m1"]


# ── POST /api/models/updates/apply ────────────────────────────────────────────


@pytest.fixture
def _no_real_pull(monkeypatch):
    """Stub the background pull body so apply never touches the network."""
    ran: list[str] = []

    async def fake_run(job, **kwargs):
        ran.append(job.model_id)
        job.state = "completed"

    # Patched on THIS module: apply_updates imports the helper lazily from
    # routes.models, so patch it there.
    import hal0.api.routes.models as models_routes

    monkeypatch.setattr(models_routes, "_run_pull_with_events", fake_run)
    return ran


def test_apply_all_available(isolated_client, monkeypatch, _no_real_pull):
    _register(isolated_client, "m1")
    _register(isolated_client, "m2")
    _stub_check(
        monkeypatch,
        [_status_row("m1", available=True), _status_row("m2", available=False)],
    )
    res = isolated_client.post("/api/models/updates/apply")
    assert res.status_code == 202
    body = res.json()
    assert [s["model_id"] for s in body["started"]] == ["m1"]
    assert body["count"] == 1
    assert body["skipped"] == []
    assert _no_real_pull == ["m1"]


def test_apply_explicit_subset(isolated_client, monkeypatch, _no_real_pull):
    _register(isolated_client, "m1")
    _register(isolated_client, "m2")
    calls = _stub_check(monkeypatch, [])
    res = isolated_client.post("/api/models/updates/apply", json={"model_ids": ["m2"]})
    assert res.status_code == 202
    body = res.json()
    assert [s["model_id"] for s in body["started"]] == ["m2"]
    # Explicit ids skip the check round-trip entirely.
    assert calls == []


def test_apply_skips_models_without_hf_source(isolated_client, monkeypatch, _no_real_pull):
    _register(isolated_client, "scanned", hf=False)
    res = isolated_client.post("/api/models/updates/apply", json={"model_ids": ["scanned"]})
    assert res.status_code == 202
    body = res.json()
    assert body["started"] == []
    assert body["skipped"] == [{"model_id": "scanned", "reason": "no_hf_source"}]


def test_apply_skips_active_pull(isolated_app_client, monkeypatch, _no_real_pull):
    app, client = isolated_app_client
    _register(client, "m1")
    from hal0.registry.pull import make_job

    job = make_job("m1")
    job.state = "running"
    app.state.model_pull_jobs["m1"] = job

    res = client.post("/api/models/updates/apply", json={"model_ids": ["m1"]})
    assert res.status_code == 202
    body = res.json()
    assert body["started"] == []
    assert body["skipped"] == [{"model_id": "m1", "reason": "pull_active"}]


def test_apply_rejects_bad_model_ids_shape(isolated_client):
    res = isolated_client.post("/api/models/updates/apply", json={"model_ids": "m1"})
    assert res.status_code == 400
    res = isolated_client.post("/api/models/updates/apply", json={"model_ids": []})
    assert res.status_code == 400
