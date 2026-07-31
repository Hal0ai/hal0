"""Lifecycle tests for the runner-image download job (start/progress/cancel/complete).

``run_runner_pull`` is driven against a fake provider (an object exposing
``pull_image_stream(image) -> AsyncIterator[dict]``) rather than a real
podman subprocess — mirrors the ``pull_image_stream`` contract documented
on ``hal0.providers.container.ContainerProvider``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_pull import (
    RunnerPullJob,
    list_persisted_jobs,
    make_job,
    persist_pull_job,
    pull_job_file,
    run_runner_pull,
)


class _FakeProvider:
    """Yields a scripted sequence of ``pull_image_stream`` events."""

    def __init__(self, events: list[dict], *, delay: float = 0.0) -> None:
        self._events = events
        self._delay = delay
        self.closed = False

    async def pull_image_stream(self, image: str):
        for event in self._events:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield event


@pytest.fixture
def store(tmp_path: Path) -> RunnerImageStore:
    s = RunnerImageStore(db_path=tmp_path / "hal0.db")
    s.upsert(
        RunnerImage(
            id="hal0ai/hal0-toolbox-cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu", tag="latest"
        )
    )
    return s


@pytest.fixture(autouse=True)
def _isolate_var_lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0home"))


def _job() -> RunnerPullJob:
    return make_job("hal0ai/hal0-toolbox-cpu", "ghcr.io/hal0ai/hal0-toolbox-cpu:latest")


class TestSuccess:
    async def test_completes_and_stamps_local_state(self, store: RunnerImageStore) -> None:
        job = _job()
        provider = _FakeProvider(
            [
                {"state": "pulling", "layer": 0, "total_layers": 2, "line": "Pulling fs layer"},
                {"state": "pulling", "layer": 1, "total_layers": 2, "line": "Pull complete"},
                {"state": "completed", "layer": 2, "total_layers": 2},
            ]
        )
        await run_runner_pull(job, store=store, provider=provider)
        assert job.state == "completed"
        assert job.layers_done == 2
        assert job.layers_total == 2
        assert job.local_path is not None
        assert job.finished_at is not None

        entry = store.get("hal0ai/hal0-toolbox-cpu")
        assert entry is not None
        assert entry.downloaded is True
        assert entry.local_path == job.local_path

    async def test_progress_events_update_job_mid_flight(self, store: RunnerImageStore) -> None:
        job = _job()
        provider = _FakeProvider(
            [
                {"state": "pulling", "layer": 1, "total_layers": 3, "line": "layer 1"},
                {"state": "pulling", "layer": 2, "total_layers": 3, "line": "layer 2"},
                {"state": "completed", "layer": 3, "total_layers": 3},
            ]
        )
        seen_layers: list[int] = []
        orig_signal = job._signal

        def _spy() -> None:
            seen_layers.append(job.layers_done)
            orig_signal()

        job._signal = _spy  # type: ignore[method-assign]
        await run_runner_pull(job, store=store, provider=provider)
        # First signal is the queued->running transition (layers_done still
        # 0); the two progress signals that follow carry the layer counts.
        assert seen_layers[0] == 0
        assert 1 in seen_layers and 2 in seen_layers
        assert job.state == "completed"


class TestFailure:
    async def test_failed_event_marks_job_failed(self, store: RunnerImageStore) -> None:
        job = _job()
        provider = _FakeProvider([{"state": "failed", "error": "exit code 1"}])
        await run_runner_pull(job, store=store, provider=provider)
        assert job.state == "failed"
        assert job.error == "exit code 1"
        assert job.error_code == "runner_image.pull_failed"
        entry = store.get("hal0ai/hal0-toolbox-cpu")
        assert entry.downloaded is False

    async def test_provider_exception_marks_job_failed_not_stuck_running(
        self, store: RunnerImageStore
    ) -> None:
        job = _job()

        class _BoomProvider:
            async def pull_image_stream(self, image: str):
                raise RuntimeError("subprocess exec failed")
                yield  # pragma: no cover - unreachable, satisfies generator syntax

        await run_runner_pull(job, store=store, provider=_BoomProvider())
        assert job.state == "failed"
        assert "subprocess exec failed" in (job.error or "")


class TestCancel:
    async def test_cancel_requested_mid_stream_marks_cancelled(
        self, store: RunnerImageStore
    ) -> None:
        job = _job()
        provider = _FakeProvider(
            [
                {"state": "pulling", "layer": 1, "total_layers": 5, "line": "layer 1"},
                {"state": "pulling", "layer": 2, "total_layers": 5, "line": "layer 2"},
                {"state": "completed", "layer": 5, "total_layers": 5},
            ],
            delay=0.01,
        )

        async def _driver() -> None:
            await run_runner_pull(job, store=store, provider=provider)

        task = asyncio.create_task(_driver())
        await asyncio.sleep(0.015)
        job.cancel_requested = True
        await task

        assert job.state == "cancelled"
        entry = store.get("hal0ai/hal0-toolbox-cpu")
        assert entry.downloaded is False


class TestPersistence:
    def test_persist_and_reload_snapshot(self, store: RunnerImageStore) -> None:
        job = _job()
        job.state = "completed"
        job.layers_done = 3
        job.layers_total = 3
        persist_pull_job(job)

        path = pull_job_file(job.image_id)
        assert path.exists()

        snapshots = list_persisted_jobs()
        assert any(s.get("image_id") == job.image_id for s in snapshots)
