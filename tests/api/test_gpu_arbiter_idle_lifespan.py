"""Lifespan wiring for the GpuArbiter idle-restore loop (Phase D, Task D6).

The api lifespan owns long-running background tasks (model-cache refresh,
lemond log bridge, …): created via ``asyncio.create_task`` at startup and
cancelled + awaited on shutdown. The arbiter idle-restore loop follows the
same pattern — this pins that it starts with the app (exposed on
``app.state.gpu_arbiter_idle_task``) and is cancelled cleanly at shutdown.

Mirrors the create_app + ``with TestClient(app)`` lifespan-exercise pattern
of ``tests/api/test_memory_gate.py``.
"""

from __future__ import annotations

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
