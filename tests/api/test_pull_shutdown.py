"""Issue #1225: `systemctl restart hal0-api` must not hang on a live model pull.

Covers the three pieces of the fix:

  * an in-flight pull is tracked as a detached ``asyncio.Task``
    (``app.state.model_pull_tasks``), not a Starlette BackgroundTask, so its
    HTTP request returns immediately instead of keeping the connection open
    for the whole download;
  * lifespan shutdown cancels those tasks and waits for them with a bound,
    so shutdown itself stays fast;
  * the pull SSE stream notices a shutdown in progress and closes promptly
    instead of blocking on it.

``run_pull`` itself is patched with fakes — the streaming/resume mechanics
are covered separately in ``tests/registry/test_pull.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.registry import pull_jobs as pull_jobs_module
from hal0.registry.pull import PullJob

pytestmark = pytest.mark.usefixtures("tmp_hal0_home")


@pytest.fixture
def app_isolated(tmp_hal0_home: str) -> Iterator[FastAPI]:
    yield create_app()


@pytest.fixture
def hanging_run_pull(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch run_pull with a fake that hangs until its task is cancelled.

    Mirrors the real contract (hal0.registry.pull.run_pull): on
    asyncio.CancelledError it records ``state = "cancelled"``, signals, and
    re-raises — this is exactly the behaviour the shutdown path depends on.
    """
    state: dict[str, Any] = {"started": asyncio.Event()}

    async def fake(job: PullJob, *, hf_repo: str, hf_file: str, **kw: Any) -> None:
        job.state = "running"
        job.bytes_total = 1024
        job._signal()
        state["started"].set()
        try:
            await asyncio.Event().wait()  # hangs forever unless cancelled
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.finished_at = time.time()
            job._signal()
            raise

    monkeypatch.setattr(pull_jobs_module, "run_pull", fake)
    return state


def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def test_pull_request_returns_before_download_finishes(
    app_isolated: FastAPI, hanging_run_pull: dict[str, Any]
) -> None:
    """The POST must not block on the (never-finishing) fake download —
    proof that the pull no longer keeps its HTTP connection open for the
    whole transfer."""
    with TestClient(app_isolated) as c:
        started = time.monotonic()
        r = c.post("/api/models/qwen3-4b/pull")
        elapsed = time.monotonic() - started
        assert r.status_code == 202, r.text
        assert elapsed < 2.0, f"POST /pull took {elapsed}s against a hanging download"

        _wait_for(hanging_run_pull["started"].is_set)
        task = app_isolated.state.model_pull_tasks.get("qwen3-4b")
        assert task is not None
        assert not task.done(), "the detached pull task must still be running"

        # Cancel via the shutdown machinery below rather than leaving a
        # hanging task across the TestClient's own teardown.
        task.cancel()


def test_shutdown_cancels_inflight_pull_within_bound(
    app_isolated: FastAPI, hanging_run_pull: dict[str, Any]
) -> None:
    """Shutdown must cancel a still-running pull and finish promptly — the
    core of #1225 (previously the lifespan finally block never touched
    in-flight pulls at all, so shutdown could hang indefinitely)."""
    cm = TestClient(app_isolated)
    client = cm.__enter__()
    try:
        r = client.post("/api/models/qwen3-4b/pull")
        assert r.status_code == 202, r.text
        _wait_for(hanging_run_pull["started"].is_set)

        task = app_isolated.state.model_pull_tasks.get("qwen3-4b")
        assert task is not None and not task.done()

        shutdown_started = time.monotonic()
    finally:
        cm.__exit__(None, None, None)
    shutdown_elapsed = time.monotonic() - shutdown_started

    assert shutdown_elapsed < 5.0, f"shutdown took {shutdown_elapsed}s to cancel the pull"
    assert task.cancelled() or task.done()
    job = app_isolated.state.model_pull_jobs["qwen3-4b"]
    assert job.state == "cancelled"
    assert app_isolated.state.shutting_down.is_set()


def test_shutdown_with_no_active_pulls_is_a_no_op(app_isolated: FastAPI) -> None:
    """No in-flight pulls → shutdown doesn't wait on anything (regression
    guard: the new cancellation step must be a fast no-op in the common
    case, not add latency to every restart)."""
    with TestClient(app_isolated):
        pass  # nothing else to assert — a hang here would time out the test


def test_pull_stream_closes_promptly_once_shutdown_flag_is_set(
    app_isolated: FastAPI, hanging_run_pull: dict[str, Any]
) -> None:
    """A client (re)subscribing to the SSE progress stream while hal0-api is
    shutting down must not be left hanging on the connection — the generator
    checks ``app.state.shutting_down`` each iteration and closes rather than
    waiting out its normal 5s keep-alive tick (or the job reaching a terminal
    state, which depends on the pull's own task being cancelled first)."""
    with TestClient(app_isolated) as c:
        r = c.post("/api/models/qwen3-4b/pull")
        assert r.status_code == 202, r.text
        _wait_for(hanging_run_pull["started"].is_set)

        # Simulate the flag flip that _shutdown_pull_jobs performs, without
        # tearing down the whole app — isolates the generator's own
        # responsiveness to the flag from the task-cancellation path
        # exercised by test_shutdown_cancels_inflight_pull_within_bound.
        app_isolated.state.shutting_down.set()

        started = time.monotonic()
        with c.stream("GET", "/api/models/qwen3-4b/pull/stream") as resp:
            body = "".join(resp.iter_text())
        elapsed = time.monotonic() - started

        assert elapsed < 2.0, f"SSE stream took {elapsed}s to close once shutting_down was set"
        assert "data:" in body

        task = app_isolated.state.model_pull_tasks.get("qwen3-4b")
        if task is not None:
            task.cancel()


def test_startup_auto_resumes_interrupted_pull(tmp_hal0_home: str) -> None:
    """A pull-job snapshot left non-terminal by a killed prior process (its
    on-disk state is "queued" — run_pull only durably persists at pull START
    and at a TERMINAL state, so a crash mid-download never gets to flush
    "running") is automatically resumed on the next hal0-api startup rather
    than requiring the operator to notice and re-POST.
    """
    from hal0.registry.model import Model
    from hal0.registry.pull import make_job, persist_pull_job
    from hal0.registry.store import ModelRegistry

    registry = ModelRegistry()
    registry.add(
        Model(
            id="user.Interrupted",
            name="user.Interrupted",
            path="/tmp/does-not-exist-yet.gguf",
            hf_repo="org/interrupted-GGUF",
            hf_filename="interrupted.gguf",
        )
    )
    stuck_job = make_job("user.Interrupted")
    persist_pull_job(stuck_job)  # writes state="queued" — the pre-crash snapshot

    calls: list[dict[str, Any]] = []

    async def fake_run_pull(job: PullJob, *, hf_repo: str, hf_file: str, **kw: Any) -> None:
        calls.append({"hf_repo": hf_repo, "hf_file": hf_file})
        job.state = "completed"
        job.bytes_downloaded = job.bytes_total = 2048
        job.finished_at = time.time()
        job._signal()

    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(pull_jobs_module, "run_pull", fake_run_pull)
    try:
        app = create_app()
        with TestClient(app) as c:
            _wait_for(lambda: len(calls) == 1)
            r = c.get("/api/models/user.Interrupted/pull/status")
            assert r.status_code == 200, r.text
            assert r.json()["state"] == "completed"
    finally:
        mp.undo()

    assert calls == [{"hf_repo": "org/interrupted-GGUF", "hf_file": "interrupted.gguf"}]


def test_startup_does_not_auto_resume_a_completed_pull(tmp_hal0_home: str) -> None:
    """A stale non-terminal snapshot for a model whose bytes already landed
    on disk (the terminal persist can itself fail-soft) must not trigger a
    wasteful re-pull."""
    from hal0.registry.model import Model
    from hal0.registry.pull import make_job, persist_pull_job
    from hal0.registry.store import ModelRegistry

    installed = tmp_hal0_home + "/already-installed.gguf"
    with open(installed, "wb") as f:
        f.write(b"gguf-bytes")

    registry = ModelRegistry()
    registry.add(
        Model(
            id="user.AlreadyDone",
            name="user.AlreadyDone",
            path=installed,
            hf_repo="org/already-done-GGUF",
            hf_filename="done.gguf",
        )
    )
    stuck_job = make_job("user.AlreadyDone")
    persist_pull_job(stuck_job)

    calls: list[dict[str, Any]] = []

    async def fake_run_pull(job: PullJob, **kw: Any) -> None:
        calls.append(kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(pull_jobs_module, "run_pull", fake_run_pull)
    try:
        app = create_app()
        with TestClient(app):
            pass
    finally:
        mp.undo()

    assert calls == [], "a model whose bytes already exist must not be re-pulled"
