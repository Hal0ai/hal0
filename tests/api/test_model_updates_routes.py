"""Route tests for the HF model-update surface.

Covers:
  * GET  /api/models/updates          — report available updates
  * POST /api/models/updates/check    — force-refresh variant
  * POST /api/models/update-all       — re-pull every stale model

The real HF probe (``check_updates``) and the streaming ``run_pull`` are
patched so tests exercise routing + orchestration, not the network.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.registry import pull as pull_module
from hal0.registry import updates as upd_module
from hal0.registry.model import Model
from hal0.registry.pull import PullJob
from hal0.registry.updates import ModelUpdateInfo

SHA_OLD = "a" * 64
SHA_NEW = "b" * 64


@pytest.fixture
def app_isolated(tmp_hal0_home: str) -> Iterator[FastAPI]:
    yield create_app()


@pytest.fixture
def client_isolated(app_isolated: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_isolated) as c:
        yield c


@pytest.fixture
def fake_run_pull(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(job: PullJob, *, hf_repo: str, hf_file: str, **kw: Any) -> None:
        calls.append({"job": job, "hf_repo": hf_repo, "hf_file": hf_file, **kw})
        job.state = "completed"
        job.finished_at = time.time()
        job._signal()

    monkeypatch.setattr(pull_module, "run_pull", fake)
    from hal0.api.routes import models as model_routes

    monkeypatch.setattr(model_routes, "run_pull", fake)
    return calls


def _register(app: FastAPI, model_id: str, *, sha: str = SHA_OLD) -> None:
    app.state.model_registry.add(
        Model(
            id=model_id,
            name=model_id,
            path=f"/var/lib/hal0/models/{model_id}/m.gguf",
            hf_repo="org/repo",
            hf_filename="m.gguf",
            capabilities=["chat"],
            metadata={"sha256": sha},
        )
    )


def _patch_check(monkeypatch: pytest.MonkeyPatch, infos: list[ModelUpdateInfo]) -> dict[str, Any]:
    seen: dict[str, Any] = {"force": None, "calls": 0}

    async def fake_check(
        models: list[Any], *, force: bool = False, client: Any = None
    ) -> list[ModelUpdateInfo]:
        seen["force"] = force
        seen["calls"] += 1
        return infos

    monkeypatch.setattr(upd_module, "check_updates", fake_check)
    return seen


# ── GET /api/models/updates ─────────────────────────────────────────────────


def test_list_updates_reports_available_count(
    client_isolated: TestClient, app_isolated: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(app_isolated, "qwen")
    seen = _patch_check(
        monkeypatch,
        [
            ModelUpdateInfo(
                model_id="qwen",
                hf_repo="org/repo",
                hf_filename="m.gguf",
                update_available=True,
                current_sha=SHA_OLD,
                remote_sha=SHA_NEW,
            )
        ],
    )
    r = client_isolated.get("/api/models/updates")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["available"] == 1
    assert body["updates"][0]["model_id"] == "qwen"
    assert body["updates"][0]["update_available"] is True
    assert seen["force"] is False


def test_list_updates_does_not_collide_with_model_id_route(
    client_isolated: TestClient, app_isolated: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression guard: "/updates" must resolve to the report route, not
    # GET /{model_id} with model_id="updates".
    _patch_check(monkeypatch, [])
    r = client_isolated.get("/api/models/updates")
    assert r.status_code == 200
    assert "updates" in r.json()


# ── POST /api/models/updates/check ──────────────────────────────────────────


def test_check_forces_refresh(
    client_isolated: TestClient, app_isolated: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _patch_check(monkeypatch, [])
    r = client_isolated.post("/api/models/updates/check")
    assert r.status_code == 200, r.text
    assert seen["force"] is True


# ── POST /api/models/update-all ─────────────────────────────────────────────


def test_update_all_kicks_pull_for_stale_models(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(app_isolated, "qwen")
    _patch_check(
        monkeypatch,
        [
            ModelUpdateInfo(
                model_id="qwen",
                hf_repo="org/repo",
                hf_filename="m.gguf",
                update_available=True,
                current_sha=SHA_OLD,
                remote_sha=SHA_NEW,
            )
        ],
    )
    r = client_isolated.post("/api/models/update-all")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["started"][0]["model_id"] == "qwen"
    # The pull background task actually ran against the resolved coords.
    assert len(fake_run_pull) == 1
    assert fake_run_pull[0]["hf_repo"] == "org/repo"
    assert fake_run_pull[0]["hf_file"] == "m.gguf"


def test_update_all_skips_when_nothing_stale(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(app_isolated, "qwen")
    _patch_check(
        monkeypatch,
        [
            ModelUpdateInfo(
                model_id="qwen",
                hf_repo="org/repo",
                hf_filename="m.gguf",
                update_available=False,
                current_sha=SHA_OLD,
                remote_sha=SHA_OLD,
            )
        ],
    )
    r = client_isolated.post("/api/models/update-all")
    assert r.status_code == 202, r.text
    assert r.json()["count"] == 0
    assert fake_run_pull == []


def test_update_all_skips_model_already_pulling(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(app_isolated, "qwen")
    # Seed an in-flight job so update-all defers to it instead of duplicating.
    running = PullJob(job_id="existing", model_id="qwen", state="running")
    app_isolated.state.model_pull_jobs["qwen"] = running
    _patch_check(
        monkeypatch,
        [
            ModelUpdateInfo(
                model_id="qwen",
                hf_repo="org/repo",
                hf_filename="m.gguf",
                update_available=True,
                current_sha=SHA_OLD,
                remote_sha=SHA_NEW,
            )
        ],
    )
    r = client_isolated.post("/api/models/update-all")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["skipped"][0]["model_id"] == "qwen"
    assert body["skipped"][0]["state"] == "running"
    assert fake_run_pull == []
