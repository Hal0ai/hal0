"""Lifespan wiring for the GpuArbiter idle-restore loop (Phase D, Task D6).

The api lifespan owns long-running background tasks (model-cache refresh,
arbiter idle-restore, …): created via ``asyncio.create_task`` at startup
and cancelled + awaited on shutdown. The arbiter idle-restore loop follows the
same pattern — this pins that it starts with the app (exposed on
``app.state.gpu_arbiter_idle_task``) and is cancelled cleanly at shutdown.

Mirrors the create_app + ``with TestClient(app)`` lifespan-exercise pattern
of ``tests/api/test_memory_gate.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app


def test_lifespan_starts_and_cancels_arbiter_idle_loop(tmp_hal0_home: str) -> None:
    app = create_app()
    with TestClient(app):
        task = app.state.gpu_arbiter_idle_task
        assert task is not None, "idle loop task must start with the lifespan"
        assert not task.done(), "idle loop must be running while the app is up"
    # shutdown cancels + awaits the task (no orphaned loop after lifespan exit)
    assert task.cancelled()


def test_lifespan_cancels_runner_catalogue_sync_task(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2634: the boot catalogue-sync task is cancelled at shutdown, not orphaned."""
    import asyncio

    import hal0.api as api_mod

    async def _hang(_app: object) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setenv("HAL0_RUNNER_CATALOGUE_SYNC", "1")
    monkeypatch.setattr(api_mod, "_boot_background_catalogue_sync", _hang)
    app = create_app()
    with TestClient(app):
        task = app.state.runner_catalogue_sync_task
        assert not task.done()
    assert task.cancelled()
