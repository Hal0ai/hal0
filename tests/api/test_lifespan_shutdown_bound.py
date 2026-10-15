"""The lifespan teardown is bounded per step (#2727).

A stop callback that never returns used to hold the lifespan's
``AsyncExitStack`` unwind open, so ``hal0.api.shutdown`` and uvicorn's
"Application shutdown complete." never appeared and systemd SIGKILLed
hal0-api after its stop timeout. ``_bounded_shutdown_step`` caps each step
and keeps the unwind going.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from structlog.testing import capture_logs

from hal0.api import _bounded_shutdown_step


def _make_stuck_step(release: asyncio.Event):
    """A step that survives cancellation and only ends once ``release`` is set.

    The worst case for the bound. ``release`` lets the test end it afterwards,
    so the event loop's own teardown is not left waiting on it.
    """

    async def _stuck() -> None:
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()

    return _stuck


@pytest.mark.asyncio
async def test_step_that_never_returns_does_not_block_unwind() -> None:
    later_steps: list[str] = []

    async def later() -> None:
        later_steps.append("ran")

    release = asyncio.Event()
    start = time.monotonic()
    try:
        with capture_logs() as logs:
            async with contextlib.AsyncExitStack() as stack:
                # Pushed first, so it runs last: proves the unwind continues
                # past the hung step to everything registered before it.
                stack.push_async_callback(later)
                stack.push_async_callback(
                    _bounded_shutdown_step("stuck", _make_stuck_step(release), timeout_s=0.2)
                )
        elapsed = time.monotonic() - start
    finally:
        release.set()
        await asyncio.sleep(0)

    assert elapsed < 2.0
    assert later_steps == ["ran"]
    timed_out = [e for e in logs if e["event"] == "hal0.api.shutdown_step_timed_out"]
    assert timed_out and timed_out[0]["step"] == "stuck"
    assert timed_out[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_step_that_raises_is_logged_and_unwind_continues() -> None:
    later_steps: list[str] = []

    async def later() -> None:
        later_steps.append("ran")

    async def boom() -> None:
        raise RuntimeError("close failed")

    with capture_logs() as logs:
        async with contextlib.AsyncExitStack() as stack:
            stack.push_async_callback(later)
            stack.push_async_callback(_bounded_shutdown_step("boom", boom, timeout_s=1.0))

    assert later_steps == ["ran"]
    failed = [e for e in logs if e["event"] == "hal0.api.shutdown_step_failed"]
    assert failed and failed[0]["step"] == "boom"
    assert "close failed" in failed[0]["error"]


@pytest.mark.asyncio
async def test_fast_step_runs_to_completion_without_warning() -> None:
    ran: list[str] = []

    async def quick() -> None:
        await asyncio.sleep(0)
        ran.append("quick")

    with capture_logs() as logs:
        await _bounded_shutdown_step("quick", quick, timeout_s=1.0)()

    assert ran == ["quick"]
    assert not [e for e in logs if e["event"].startswith("hal0.api.shutdown_step_")]


@pytest.mark.asyncio
async def test_outer_cancel_cancels_the_step() -> None:
    step_cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            step_cancelled.set()
            raise

    runner = asyncio.create_task(_bounded_shutdown_step("slow", slow, timeout_s=60)())
    await asyncio.sleep(0.05)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    await asyncio.wait_for(step_cancelled.wait(), timeout=1.0)


def test_lifespan_registers_every_stop_callback_through_the_bound() -> None:
    """Guard against a new teardown callback being pushed unbounded."""
    import inspect

    from hal0.api import lifespan

    src = inspect.getsource(lifespan)
    assert "stack.push_async_callback(ctx." not in src
    for name in (
        "stop_refresh_task",
        "stop_gpu_arbiter_idle_loop",
        "stop_image_drift_task",
        "stop_runner_catalogue_sync_task",
        "stop_memory_reprobe_task",
        "metrics_service.stop",
    ):
        assert name in src
