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


class _Calls(list):  # type: ignore[type-arg]
    """The fake's call log; reading it waits briefly for the first call.

    The pull route schedules the download with ``asyncio.create_task`` and
    returns 202 first, so under ``TestClient`` the task runs on the app's
    loop thread concurrently with the test. ``len()`` and indexing wait up to
    3 s for a call to land; an equality check (``== []``) does not wait.
    """

    def _settle(self) -> None:
        deadline = time.monotonic() + 3.0
        while not list.__len__(self) and time.monotonic() < deadline:
            time.sleep(0.01)

    def __len__(self) -> int:
        self._settle()
        return list.__len__(self)

    def __getitem__(self, index: Any) -> Any:
        self._settle()
        return list.__getitem__(self, index)


def _wait_for_fake_pull(app: FastAPI, model_id: str) -> None:
    """Wait for the fake pull the route scheduled to finish.

    The route starts it with ``asyncio.create_task`` and returns 202 first,
    so under ``TestClient`` it runs on the loop thread concurrently with the
    test, and would overwrite a state the test sets in the meantime.
    """
    deadline = time.monotonic() + 3.0
    while app.state.model_pull_jobs[model_id].state != "completed":
        assert time.monotonic() < deadline, "the fake pull never completed"
        time.sleep(0.01)


@pytest.fixture
def fake_run_pull(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch run_pull to record calls + drive job state synchronously.

    Returns a list the test can inspect to assert what was scheduled.
    The fake transitions the job straight to ``completed`` so SSE / status
    routes see a terminal frame on their first poll.
    """
    calls: list[dict[str, Any]] = _Calls()

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
    # The pull-job orchestration (P3-routers inc 1) lives in
    # hal0.registry.pull_jobs, which binds run_pull at module load — patch
    # its binding so the fake reaches the BackgroundTasks invocation. (The
    # installer route no longer binds run_pull directly: WS-E / #1108 moved
    # it behind run_pull_and_activate, which imports run_pull lazily from
    # hal0.registry.pull — already patched above via pull_module.)
    from hal0.registry import pull_jobs

    monkeypatch.setattr(pull_jobs, "run_pull", fake)
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


def test_pull_body_chat_template_seeds_the_fact(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """WS-6 — a chat_template in the pull body is pinned onto the freshly
    seeded registry row's chat_template fact at pull start (the modal closes on start
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
    assert entry.chat_template == "chatml"


def test_pull_body_chat_template_auto_seeds_no_fact(
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
    assert entry.chat_template is None


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
    assert entry.chat_template == "llama3"


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


# ── #2775: body-supplied HF coordinates are validated before seeding ────────


@pytest.mark.parametrize(
    "extra",
    [
        # hf_filename climbing out of the models tree, or rooted elsewhere.
        {"hf_repo": "org/name", "hf_filename": "../../etc/passwd"},
        {"hf_repo": "org/name", "hf_filename": "sub/../../x.gguf"},
        {"hf_repo": "org/name", "hf_filename": "/etc/passwd"},
        {"hf_repo": "org/name", "hf_filename": "sub\\..\\x.gguf"},
        # hf_repo that is not an org/name coordinate.
        {"hf_repo": "not-a-slug", "hf_filename": "m.gguf"},
        {"hf_repo": "org/name/extra", "hf_filename": "m.gguf"},
        {"hf_repo": "../name", "hf_filename": "m.gguf"},
        {"hf_repo": "org/..", "hf_filename": "m.gguf"},
        {"hf_repo": "org name/x", "hf_filename": "m.gguf"},
        # The sidecar coordinates ride the same body and get the same check.
        {"hf_repo": "org/name", "hf_filename": "m.gguf", "mmproj_filename": "../mm.gguf"},
        {"hf_repo": "org/name", "hf_filename": "m.gguf", "draft_filename": "/abs/d.gguf"},
        {
            "hf_repo": "org/name",
            "hf_filename": "m.gguf",
            "draft_filename": "d.gguf",
            "draft_repo": "bad",
        },
    ],
)
def test_pull_body_rejects_unsafe_hf_coordinates(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
    extra: dict[str, str],
) -> None:
    """#2775: a malformed repo or a filename that is absolute or climbs
    with ``..`` is a 422 ``model.invalid_source`` — no row seeded, no pull.
    """
    r = client_isolated.post("/api/models/user.bad-coords/pull", json=extra)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.invalid_source"
    assert fake_run_pull == []
    from hal0.registry.store import ModelNotFound

    with pytest.raises(ModelNotFound):
        app_isolated.state.model_registry.get("user.bad-coords")


def test_pull_body_accepts_repo_subfolder_filename(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """A relative filename inside a repo subfolder is a legitimate HF
    coordinate (split quants often live under ``Q4_K_M/``) and still pulls.
    """
    from hal0.config import paths

    r = client_isolated.post(
        "/api/models/user.sub/pull",
        json={"hf_repo": "org/Some-Model_1.5-GGUF", "hf_filename": "Q4_K_M/m.gguf"},
    )
    assert r.status_code == 202, r.text
    entry = app_isolated.state.model_registry.get("user.sub")
    assert Path(entry.path).resolve().is_relative_to(paths.models_dir().resolve())


def test_seed_registry_keeps_provisional_path_inside_models_dir(
    client_isolated: TestClient,
    app_isolated: FastAPI,
) -> None:
    """#2775: the seeded row's provisional path never leaves models_dir(),
    even for a model id the router would pass through verbatim (``..``).
    """
    from types import SimpleNamespace

    from hal0.config import paths
    from hal0.registry.pull_jobs import seed_registry_from_body

    request = SimpleNamespace(app=app_isolated)
    seed_registry_from_body(request, "..", "org/name", "m.gguf", None)  # type: ignore[arg-type]
    entry = app_isolated.state.model_registry.get("..")
    root = paths.models_dir().resolve()
    assert Path(entry.path).resolve().is_relative_to(root)
    assert Path(entry.path).resolve() != root / "m.gguf"


def test_pull_idempotent_when_already_running(
    client_isolated: TestClient,
    app_isolated: FastAPI,
    fake_run_pull: list[dict[str, Any]],
) -> None:
    """Two POSTs back-to-back don't spawn two jobs."""
    # First call completes via the fake. Manually re-set state to
    # ``running`` so the second call hits the "already in flight" branch.
    client_isolated.post("/api/models/qwen3-4b/pull")
    _wait_for_fake_pull(app_isolated, "qwen3-4b")
    jobs = app_isolated.state.model_pull_jobs
    jobs["qwen3-4b"].state = "running"
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    body = r.json()
    assert body.get("resumed") is True


def test_pull_status_returns_job_dict(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    client_isolated.post("/api/models/qwen3-4b/pull")
    # The scheduled pull runs on the loop thread concurrently with the test.
    deadline = time.monotonic() + 3.0
    while True:
        r = client_isolated.get("/api/models/qwen3-4b/pull/status")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["state"] == "completed" or time.monotonic() > deadline:
            break
        time.sleep(0.01)
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
    _wait_for_fake_pull(app_isolated, "qwen3-4b")
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
    from hal0.registry import pull_jobs
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
        pull_jobs, "get_curated", lambda mid: curated if mid == "vision-pick" else None
    )

    r = client_isolated.post("/api/models/vision-pick/pull")
    assert r.status_code == 202, r.text
    assert r.json()["mmproj_file"] == "mmproj-F16.gguf"
    assert fake_run_pull[0]["mmproj_file"] == "mmproj-F16.gguf"


# ── #1394: the vision label needs a projector on the pull path too ───────────
#
# The Add-by-HF modal writes its row through POST /pull (seed_registry_from_body),
# NOT through POST /api/models — so the models-write screen never sees it. The
# same invariant is enforced here, against the sidecar the pull will install.


def test_pull_vision_label_without_mmproj_rejected(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """Ticking ``vision`` in the Add-by-HF modal with no mmproj file selected
    seeds a projector-less vision row today. Refuse it on the wire (#1394)."""
    r = client_isolated.post(
        "/api/models/user.NoProjector/pull",
        json={
            "hf_repo": "org/vision-GGUF",
            "hf_filename": "vision-Q4_K_M.gguf",
            "labels": ["chat", "vision"],
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.vision_requires_mmproj"
    # Nothing seeded, nothing scheduled.
    assert client_isolated.get("/api/models/user.NoProjector").status_code == 404
    assert fake_run_pull == []


def test_pull_non_vision_labels_unaffected(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    """The guard is scoped to the vision label — an ordinary chat pull with no
    mmproj still goes through."""
    r = client_isolated.post(
        "/api/models/user.PlainChat/pull",
        json={
            "hf_repo": "org/chat-GGUF",
            "hf_filename": "chat-Q4_K_M.gguf",
            "labels": ["chat"],
        },
    )
    assert r.status_code == 202, r.text
    assert len(fake_run_pull) == 1


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

    # The background task then persists its terminal snapshot, which includes
    # bytes_downloaded from the fake. Wait for it: the route returns before
    # the task runs.
    deadline = time.monotonic() + 3.0
    while on_disk.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        on_disk = json.loads(job_file.read_text(encoding="utf-8"))
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


# ── ADR-0031 §4: a paired drafter is screened before it is downloaded ───────

_TARGET_HEADER: dict[str, Any] = {
    "general.architecture": "qwen3",
    "qwen3.embedding_length": 4096,
    "qwen3.block_count": 36,
}
_DRAFT_HEADER: dict[str, Any] = {
    "general.architecture": "dflash",
    "dflash.embedding_length": 4096,
    "dflash.block_size": 8,
    "dflash.target_layers": [2, 19, 35],
    "lilicorr.candidate_topk": 4,
}
_DRAFT_BODY: dict[str, Any] = {
    "hf_repo": "org/target-GGUF",
    "hf_filename": "target-Q4_K_M.gguf",
    "draft_repo": "org/drafters-GGUF",
    "draft_filename": "drafter-lilicorr-Q8_0.gguf",
}


def _assert_nothing_seeded(client: TestClient, model_id: str) -> None:
    from hal0.registry.store import ModelNotFound

    with pytest.raises(ModelNotFound):
        client.app.state.model_registry.get(model_id)  # type: ignore[attr-defined]


def _stub_headers(
    monkeypatch: pytest.MonkeyPatch, by_file: dict[str, dict[str, Any] | None]
) -> list[tuple[str, str]]:
    from hal0.services import models_service
    from hal0.upstreams import hf_header

    models_service.INSPECT_DRAFT_CACHE.clear()
    reads: list[tuple[str, str]] = []

    async def fake(repo: str, filename: str, **_kw: Any) -> dict[str, Any] | None:
        reads.append((repo, filename))
        return by_file.get(filename)

    monkeypatch.setattr(hf_header, "fetch_gguf_header", fake)
    return reads


def test_pull_route_threads_draft_to_run_pull(
    client_isolated: TestClient,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_headers(
        monkeypatch,
        {"target-Q4_K_M.gguf": _TARGET_HEADER, "drafter-lilicorr-Q8_0.gguf": _DRAFT_HEADER},
    )
    r = client_isolated.post(
        "/api/models/user.Draftee/pull",
        json={
            "hf_repo": "org/target-GGUF",
            "hf_filename": "target-Q4_K_M.gguf",
            "draft_repo": "org/drafters-GGUF",
            "draft_filename": "drafter-lilicorr-Q8_0.gguf",
        },
    )
    assert r.status_code == 202, r.text
    assert r.json()["draft_file"] == "drafter-lilicorr-Q8_0.gguf"
    assert fake_run_pull[0]["draft_file"] == "drafter-lilicorr-Q8_0.gguf"
    assert fake_run_pull[0]["draft_repo"] == "org/drafters-GGUF"


def test_pull_without_draft_passes_none(
    client_isolated: TestClient, fake_run_pull: list[dict[str, Any]]
) -> None:
    r = client_isolated.post("/api/models/qwen3-4b/pull")
    assert r.status_code == 202, r.text
    assert "draft_file" not in r.json()
    assert fake_run_pull[0]["draft_file"] is None
    assert fake_run_pull[0]["draft_repo"] is None


def test_pull_route_screens_draft_fit_before_download(
    client_isolated: TestClient,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_target = {**_TARGET_HEADER, "qwen3.embedding_length": 2048}
    reads = _stub_headers(
        monkeypatch,
        {"target-Q4_K_M.gguf": wrong_target, "drafter-lilicorr-Q8_0.gguf": _DRAFT_HEADER},
    )
    r = client_isolated.post(
        "/api/models/user.Draftee/pull",
        json={
            "hf_repo": "org/target-GGUF",
            "hf_filename": "target-Q4_K_M.gguf",
            "draft_repo": "org/drafters-GGUF",
            "draft_filename": "drafter-lilicorr-Q8_0.gguf",
        },
    )
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.draft_mismatch"
    assert err["details"]["reasons"]
    assert fake_run_pull == [], "no download may be scheduled on a mismatch"
    assert len(reads) == 2, "only the two header reads may touch the Hub"
    _assert_nothing_seeded(client_isolated, "user.Draftee")


def test_pull_refuses_when_draft_header_unreadable(
    client_isolated: TestClient,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _stub_headers(monkeypatch, {"target-Q4_K_M.gguf": _TARGET_HEADER})
    r = client_isolated.post(
        "/api/models/user.Draftee/pull",
        json={
            "hf_repo": "org/target-GGUF",
            "hf_filename": "target-Q4_K_M.gguf",
            "draft_repo": "org/drafters-GGUF",
            "draft_filename": "drafter-lilicorr-Q8_0.gguf",
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "hf.header_unreadable"
    assert fake_run_pull == []
    assert len(reads) == 2, "only the two header reads may touch the Hub"
    _assert_nothing_seeded(client_isolated, "user.Draftee")


def test_pull_draft_repo_defaults_to_hf_repo(
    client_isolated: TestClient,
    fake_run_pull: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _stub_headers(
        monkeypatch,
        {"target-Q4_K_M.gguf": _TARGET_HEADER, "drafter-lilicorr-Q8_0.gguf": _DRAFT_HEADER},
    )
    r = client_isolated.post(
        "/api/models/user.Draftee/pull",
        json={
            "hf_repo": "org/target-GGUF",
            "hf_filename": "target-Q4_K_M.gguf",
            "draft_filename": "drafter-lilicorr-Q8_0.gguf",
        },
    )
    assert r.status_code == 202, r.text
    assert ("org/target-GGUF", "drafter-lilicorr-Q8_0.gguf") in reads
    assert fake_run_pull[0]["draft_repo"] == "org/target-GGUF"
