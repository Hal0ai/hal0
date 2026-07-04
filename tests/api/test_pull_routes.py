"""Route tests for /api/models/{id}/pull*.

The real ``run_pull`` body is patched so tests don't hit HuggingFace —
we exercise the routing surface, job state machine, and slot TOML
write, not the HTTP streaming itself (that's tested separately in
``tests/registry/test_pull.py``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.registry import pull as pull_module
from hal0.registry.pull import PullJob

# ── shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def app_isolated(tmp_hal0_home: str) -> Iterator[FastAPI]:
    """Build an app under HAL0_HOME so atomic writes are tmp-scoped."""
    yield create_app()


@pytest.fixture
def client_isolated(app_isolated: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_isolated) as c:
        yield c


@pytest.fixture
def fake_run_pull(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch run_pull to record calls + drive job state synchronously.

    Returns a list the test can inspect to assert what was scheduled.
    The fake transitions the job straight to ``completed`` so SSE / status
    routes see a terminal frame on their first poll.
    """
    calls: list[dict[str, Any]] = []

    async def fake(job: PullJob, *, hf_repo: str, hf_file: str, **kw: Any) -> None:
        calls.append({"job": job, "hf_repo": hf_repo, "hf_file": hf_file, **kw})
        job.state = "running"
        job.bytes_total = 1024
        job.bytes_downloaded = 1024
        job.state = "completed"
        job.finished_at = time.time()
        # Pulse so any awaiting SSE generator wakes.
        job._signal()

    monkeypatch.setattr(pull_module, "run_pull", fake)
    # The routes import run_pull at module load — also patch their
    # binding so the fake reaches the BackgroundTasks invocation.
    from hal0.api.routes import installer as installer_routes
    from hal0.api.routes import models as model_routes

    monkeypatch.setattr(installer_routes, "run_pull", fake)
    monkeypatch.setattr(model_routes, "run_pull", fake)
    return calls


# ── POST /api/models/{id}/pull ──────────────────────────────────────────────


def test_pull_returns_job_handle_and_kicks_background_task(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """A curated id resolves to its HF coordinates; a job handle returns."""
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["model_id"] == "qwen3-4b"
    assert body["state"] in ("queued", "running", "completed")
    assert body["hf_repo"].startswith("Qwen/")
    assert body["hf_file"].endswith(".gguf")
    # Background task ran (TestClient drains background tasks before
    # returning).
    assert len(fake_run_pull) == 1
    assert fake_run_pull[0]["hf_repo"] == body["hf_repo"]


def test_pull_unknown_model_returns_invalid_source(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """A model with no HF coordinates and no curated entry → 422."""
    r = client_isolated.post("/api/models/nonsense-id/pull")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "model.invalid_source"
    assert fake_run_pull == []


def test_pull_body_hf_coords_used_when_id_not_registered(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """Add-by-HF-coords flow — POST a body with hf_repo + hf_filename and
    the pull starts even when the id is brand new (not in registry, not
    curated). A registry row is seeded so the dashboard can show progress
    against a real entry. Regression for issue: the AddByHfModal sent
    those fields, the backend ignored them, and a freshly-inspected
    ``user.<NewName>`` always 422'd.
    """
    new_id = "user.Qwen3.6-27B-MTP"
    r = client_isolated.post(
        f"/api/models/{new_id}/pull",
        json={
            "hf_repo": "unsloth/Qwen3.6-27B-A3B-MTP-GGUF",
            "hf_filename": "Qwen3.6-27B-A3B-MTP-Q4_K_M.gguf",
            "labels": ["chat", "tool-calling"],
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["model_id"] == new_id
    assert body["hf_repo"] == "unsloth/Qwen3.6-27B-A3B-MTP-GGUF"
    assert body["hf_file"] == "Qwen3.6-27B-A3B-MTP-Q4_K_M.gguf"
    # Registry row seeded so subsequent loads find HF coordinates.
    entry = app_isolated.state.model_registry.get(new_id)
    assert entry.hf_repo == "unsloth/Qwen3.6-27B-A3B-MTP-GGUF"
    assert entry.hf_filename == "Qwen3.6-27B-A3B-MTP-Q4_K_M.gguf"
    assert "chat" in entry.capabilities
    # Background task ran with the body-supplied coords.
    assert len(fake_run_pull) == 1
    assert fake_run_pull[0]["hf_repo"] == "unsloth/Qwen3.6-27B-A3B-MTP-GGUF"


def test_pull_body_chat_template_seeds_defaults(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """WS-6 — a chat_template in the pull body is pinned onto the freshly
    seeded registry row's defaults at pull start (the modal closes on start
    and can't sequence a later PUT). It only matters at load time, so the
    default persists whether or not the pull ultimately succeeds.
    """
    new_id = "user.WithTemplate"
    r = client_isolated.post(
        f"/api/models/{new_id}/pull",
        json={
            "hf_repo": "unsloth/Some-GGUF",
            "hf_filename": "Some-Q4_K_M.gguf",
            "labels": ["chat"],
            "chat_template": "chatml",
        },
    )
    assert r.status_code == 202, r.text
    entry = app_isolated.state.model_registry.get(new_id)
    assert entry.defaults is not None
    assert entry.defaults.chat_template == "chatml"


def test_pull_body_chat_template_auto_seeds_no_default(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """The "auto" sentinel means "use the GGUF-embedded template" — it must
    NOT persist as an override (so the launcher keeps its auto behavior)."""
    new_id = "user.AutoTemplate"
    r = client_isolated.post(
        f"/api/models/{new_id}/pull",
        json={
            "hf_repo": "unsloth/Some-GGUF",
            "hf_filename": "Some-Q4_K_M.gguf",
            "chat_template": "auto",
        },
    )
    assert r.status_code == 202, r.text
    entry = app_isolated.state.model_registry.get(new_id)
    assert entry.defaults is None or entry.defaults.chat_template is None


def test_pull_body_chat_template_patches_existing_row(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """A re-pull that supplies a chat_template carries the pin onto an
    already-registered row alongside the refreshed HF coordinates."""
    from hal0.registry.model import Model

    app_isolated.state.model_registry.add(
        Model(
            id="user.RepullTemplate",
            name="user.RepullTemplate",
            path="/tmp/stub.gguf",
            hf_repo="stub/old-repo",
            hf_filename="old.gguf",
        )
    )
    r = client_isolated.post(
        "/api/models/user.RepullTemplate/pull",
        json={
            "hf_repo": "stub/new-repo",
            "hf_filename": "new.gguf",
            "chat_template": "llama3",
        },
    )
    assert r.status_code == 202, r.text
    entry = app_isolated.state.model_registry.get("user.RepullTemplate")
    assert entry.hf_filename == "new.gguf"
    assert entry.defaults is not None
    assert entry.defaults.chat_template == "llama3"


def test_pull_body_hf_coords_override_registry_entry(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """If the body supplies hf_repo + hf_filename, they win over an
    existing registry row's coords — operator retry of a different
    variant against the same id stays intentional.
    """
    from hal0.registry.model import Model

    app_isolated.state.model_registry.add(
        Model(
            id="user.SomeModel",
            name="user.SomeModel",
            path="/tmp/stub.gguf",
            hf_repo="stub/old-repo",
            hf_filename="old.gguf",
        )
    )
    r = client_isolated.post(
        "/api/models/user.SomeModel/pull",
        json={"hf_repo": "stub/new-repo", "hf_filename": "new.gguf"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["hf_repo"] == "stub/new-repo"
    assert body["hf_file"] == "new.gguf"
    # Registry row updated to reflect the new pick.
    entry = app_isolated.state.model_registry.get("user.SomeModel")
    assert entry.hf_repo == "stub/new-repo"
    assert entry.hf_filename == "new.gguf"


def test_pull_body_partial_hf_coords_falls_back_to_resolver(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """An incomplete body (only hf_repo, no hf_filename) is treated as
    "no override" and falls back to the registry/curated resolver. A
    brand-new id still 422s instead of silently using a half-set coord.
    """
    r = client_isolated.post(
        "/api/models/user.Nope/pull",
        json={"hf_repo": "stub/half-set"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "model.invalid_source"
    assert fake_run_pull == []


def test_pull_idempotent_when_already_running(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """Two POSTs back-to-back don't spawn two jobs."""
    # First call completes via the fake. Manually re-set state to
    # ``running`` so the second call hits the "already in flight" branch.
    client_isolated.post("/api/models/qwen3-4b/pull")
    jobs = app_isolated.state.model_pull_jobs
    jobs["qwen3-4b"].state = "running"
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    body = r.json()
    assert body.get("resumed") is True


def test_pull_status_returns_job_dict(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    client_isolated.post("/api/models/qwen3-4b/pull")
    r = client_isolated.get("/api/models/qwen3-4b/pull/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == "qwen3-4b"
    assert body["state"] == "completed"
    assert body["bytes_downloaded"] == body["bytes_total"]


def test_pull_status_404_when_no_job(client_isolated: TestClient) -> None:
    r = client_isolated.get("/api/models/qwen3-4b/pull/status")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model.pull_job_not_found"


def test_pull_cancel_flips_flag(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """Cancelling an in-flight job sets the cancel flag."""
    client_isolated.post("/api/models/qwen3-4b/pull")
    jobs = app_isolated.state.model_pull_jobs
    jobs["qwen3-4b"].state = "running"  # simulate live download
    r = client_isolated.post("/api/models/qwen3-4b/pull/cancel")
    assert r.status_code == 200, r.text
    assert jobs["qwen3-4b"].cancel_requested is True


def test_pull_threads_capability_to_run_pull(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """P3: the standalone /pull resolves a curated model's capability and passes
    it to run_pull so the file lands in the capability-grouped layout."""
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text
    assert len(fake_run_pull) == 1
    assert fake_run_pull[0]["capability"] == "chat"


def test_pull_body_capability_overrides(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """An explicit body.capability wins over the curated default."""
    r = client_isolated.post("/api/models/qwen3-4b/pull", json={"capability": "embed"})
    assert r.status_code == 202, r.text
    assert fake_run_pull[0]["capability"] == "embed"


# ── WS-11: mmproj_filename threading ─────────────────────────────────────────


def test_pull_body_mmproj_filename_threads_to_run_pull(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """The Add-by-HF modal's ``mmproj_filename`` reaches run_pull as
    ``mmproj_file`` so the vision sidecar downloads with the model."""
    r = client_isolated.post(
        "/api/models/user.VisionPick/pull",
        json={
            "hf_repo": "org/vision-GGUF",
            "hf_filename": "vision-Q4_K_M.gguf",
            "mmproj_filename": "mmproj-F16.gguf",
            "labels": ["chat", "vision"],
        },
    )
    assert r.status_code == 202, r.text
    assert r.json()["mmproj_file"] == "mmproj-F16.gguf"
    assert len(fake_run_pull) == 1
    assert fake_run_pull[0]["mmproj_file"] == "mmproj-F16.gguf"


def test_pull_without_mmproj_passes_none(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """Single-file pulls stay single-file: no body mmproj + no curated mmproj
    → run_pull gets mmproj_file=None and the response omits the key."""
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text
    assert "mmproj_file" not in r.json()
    assert fake_run_pull[0]["mmproj_file"] is None


def test_pull_curated_mmproj_file_is_wired(
    client_isolated: TestClient,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A curated entry that carries ``mmproj_file`` gets a two-file pull
    without the caller sending anything in the body."""
    from hal0.api.routes import models as model_routes
    from hal0.registry.curated import CuratedModel

    curated = CuratedModel(
        id="vision-pick",
        display_name="Vision Pick",
        description="test",
        family="qwen",
        size_gb=1.0,
        vram_gb_min=1.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="org/vision-GGUF",
        hf_file="vision-Q4_K_M.gguf",
        mmproj_file="mmproj-F16.gguf",
    )
    monkeypatch.setattr(
        model_routes, "get_curated", lambda mid: curated if mid == "vision-pick" else None
    )

    r = client_isolated.post("/api/models/vision-pick/pull")
    assert r.status_code == 202, r.text
    assert r.json()["mmproj_file"] == "mmproj-F16.gguf"
    assert fake_run_pull[0]["mmproj_file"] == "mmproj-F16.gguf"


# ── #626: durable pull-job store ────────────────────────────────────────────


def test_pull_persists_job_to_disk(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    tmp_hal0_home: str,
) -> None:
    """A queued pull job is written to disk under model-pull-jobs/<model_id>.json.

    The queued snapshot is persisted synchronously before the route returns
    so a status poll survives a daemon restart (the job file exists even
    before the background task runs).
    """
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text

    # The sanitised model id "qwen3-4b" maps to the same filename.
    job_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "model-pull-jobs" / "qwen3-4b.json"
    assert job_file.exists(), f"expected on-disk job record at {job_file}"
    on_disk = json.loads(job_file.read_text(encoding="utf-8"))
    assert on_disk["model_id"] == "qwen3-4b"
    assert "state" in on_disk
    # The terminal snapshot includes bytes_downloaded from the fake.
    assert on_disk["bytes_downloaded"] == 1024


def test_pull_status_falls_back_to_disk_after_restart(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    tmp_hal0_home: str,
) -> None:
    """Status poll resolves from disk even when the in-memory dict was wiped.

    Simulates an ``hal0-api`` restart mid-pull: the process-local
    ``app.state.model_pull_jobs`` dict is cleared, but the durable record
    on disk lets the status poll still resolve (no 404).
    """
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text

    # Wait for the background task to persist a terminal state.
    job_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "model-pull-jobs" / "qwen3-4b.json"
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if job_file.exists():
            rec = json.loads(job_file.read_text(encoding="utf-8"))
            if rec.get("state") not in ("queued", "running"):
                break
        time.sleep(0.05)

    # Simulate the restart: wipe the in-memory job registry.
    app_isolated.state.model_pull_jobs = {}

    # Status poll must still return 200 using the on-disk snapshot.
    s = client_isolated.get("/api/models/qwen3-4b/pull/status")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["model_id"] == "qwen3-4b"
    assert body["state"] == "completed"


def test_pull_status_reconciles_stale_inflight_to_failed_after_restart(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    tmp_hal0_home: str,
) -> None:
    """A snapshot left mid-pull (queued/running) is served as ``failed`` after a
    restart — its worker is gone so it can never progress, and the client must
    not poll a forever-``running`` state. Regression guard for the disk-fallback
    serving a non-terminal snapshot verbatim."""
    job_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "model-pull-jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "qwen3-4b.json").write_text(
        json.dumps(
            {"id": "j1", "model_id": "qwen3-4b", "state": "running", "bytes_downloaded": 512}
        ),
        encoding="utf-8",
    )
    # Fresh in-memory dict → forces the disk fallback + reconcile path.
    app_isolated.state.model_pull_jobs = {}

    s = client_isolated.get("/api/models/qwen3-4b/pull/status")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["state"] == "failed"
    assert body["error_code"] == "pull.interrupted"


def test_pull_status_reports_completed_when_registry_has_installed_model_despite_stale_snapshot(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    tmp_hal0_home: str,
    tmp_path: Path,
) -> None:
    """MR-2: a pull that actually landed must not be reported ``failed``.

    The terminal on-disk write can fail-soft (swallowed OSError), leaving a
    stale ``running`` snapshot even though the model file is present. On a
    restart the disk fallback + reconcile must cross-check the registry: when
    the id is installed and its file exists on disk, surface ``completed`` —
    not ``failed``/``pull.interrupted``.
    """
    from hal0.registry.model import Model

    model_file = tmp_path / "curated-x.gguf"
    model_file.write_bytes(b"gguf-bytes")
    app_isolated.state.model_registry.add(
        Model(
            id="curated.x",
            name="curated.x",
            path=str(model_file),
            size_bytes=model_file.stat().st_size,
        )
    )

    job_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "model-pull-jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "curated.x.json").write_text(
        json.dumps(
            {"id": "j1", "model_id": "curated.x", "state": "running", "bytes_downloaded": 512}
        ),
        encoding="utf-8",
    )
    # Fresh in-memory dict → forces the disk fallback + reconcile path.
    app_isolated.state.model_pull_jobs = {}

    s = client_isolated.get("/api/models/curated.x/pull/status")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["state"] == "completed"
    assert body.get("error_code") != "pull.interrupted"
    assert body.get("path") == str(model_file)


def test_pull_status_reports_failed_when_model_not_installed(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    tmp_hal0_home: str,
) -> None:
    """MR-2 negative twin: a genuinely-interrupted pull still fails.

    Same stale ``running`` snapshot, but the registry does NOT have the id
    (nothing landed) → the reconcile must fall through to the failed-rewrite,
    proving the ground-truth guard doesn't mask real interruptions."""
    job_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "model-pull-jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "curated.x.json").write_text(
        json.dumps(
            {"id": "j1", "model_id": "curated.x", "state": "running", "bytes_downloaded": 512}
        ),
        encoding="utf-8",
    )
    app_isolated.state.model_pull_jobs = {}

    s = client_isolated.get("/api/models/curated.x/pull/status")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["state"] == "failed"
    assert body["error_code"] == "pull.interrupted"
