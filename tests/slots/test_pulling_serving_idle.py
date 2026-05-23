"""Tests for the three slot states wired in task #10.

Covers PLAN.md §5 state machine:

  - **PULLING**  — load() flips offline→pulling→starting when the model is
    not on disk yet, and skips pulling when it is.
  - **SERVING**  — SlotManager.serving() context flips READY/IDLE → SERVING
    on the first concurrent entry and back to READY on the last exit.
  - **IDLE**     — the background sweeper demotes READY → IDLE after the
    configured idle timeout, and serving() resets the clock.

All systemctl + health-probe calls are stubbed via the shared fixtures in
``tests/slots/conftest.py`` so the suite is hermetic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState

# ── PULLING ──────────────────────────────────────────────────────────────────


async def test_load_transitions_through_pulling_when_not_cached(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """A pull_runner + cache-miss inserts PULLING before STARTING."""
    pulls: list[str] = []

    async def pull_runner(model_id: str) -> None:
        pulls.append(model_id)

    sm = SlotManager(
        pull_runner=pull_runner,
        model_cache_check=lambda _mid: False,  # always miss
    )

    seen: list[str] = []

    async def consumer() -> None:
        async for rec in sm.state_stream():
            seen.append(rec.state.value)
            if rec.state == SlotState.READY:
                return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    snap = await sm.load("primary")
    await asyncio.wait_for(task, timeout=2.0)

    assert snap.state == SlotState.READY
    assert pulls == ["qwen3-4b-q4_k_m"], "pull_runner must fire exactly once"
    # PULLING must appear, then STARTING, then WARMING, then READY.
    assert "pulling" in seen
    assert seen.index("pulling") < seen.index("starting") < seen.index("ready")


async def test_load_skips_pulling_when_model_cached(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """A pull_runner with a cached model goes straight offline → starting."""
    pulls: list[str] = []

    async def pull_runner(model_id: str) -> None:
        pulls.append(model_id)

    sm = SlotManager(
        pull_runner=pull_runner,
        model_cache_check=lambda _mid: True,  # always hit
    )

    seen: list[str] = []

    async def consumer() -> None:
        async for rec in sm.state_stream():
            seen.append(rec.state.value)
            if rec.state == SlotState.READY:
                return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    snap = await sm.load("primary")
    await asyncio.wait_for(task, timeout=2.0)

    assert snap.state == SlotState.READY
    assert pulls == [], "cached model must not trigger pull_runner"
    assert "pulling" not in seen


async def test_load_without_pull_runner_never_enters_pulling(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """No pull_runner wired → legacy offline → starting → warming → ready."""
    sm = SlotManager()  # no pull_runner
    seen: list[str] = []

    async def consumer() -> None:
        async for rec in sm.state_stream():
            seen.append(rec.state.value)
            if rec.state == SlotState.READY:
                return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await sm.load("primary")
    await asyncio.wait_for(task, timeout=2.0)
    assert "pulling" not in seen


async def test_pull_runner_failure_flips_to_error(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """A raising pull_runner surfaces as ERROR + the exception propagates."""

    class PullBoom(RuntimeError):
        pass

    async def pull_runner(_mid: str) -> None:
        raise PullBoom("network down")

    sm = SlotManager(
        pull_runner=pull_runner,
        model_cache_check=lambda _mid: False,
    )
    # Empty lemond's loaded[] so status() can't adopt to READY — the
    # model never made it into Lemonade because the pull aborted first.
    lemonade_loaded_stub["loaded"] = []

    with pytest.raises(PullBoom):
        await sm.load("primary")
    snap = await sm.status("primary")
    assert snap.state == SlotState.ERROR


# ── SERVING ─────────────────────────────────────────────────────────────────


async def test_serving_context_flips_ready_to_serving_and_back(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("primary")
    assert (await sm.status("primary")).state == SlotState.READY

    async with sm.serving("primary"):
        assert (await sm.status("primary")).state == SlotState.SERVING
        assert sm.in_flight_count("primary") == 1

    assert (await sm.status("primary")).state == SlotState.READY
    assert sm.in_flight_count("primary") == 0


async def test_serving_concurrent_requests_keep_state_serving(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """N concurrent requests must NOT toggle READY↔SERVING mid-flight."""
    sm = SlotManager()
    await sm.load("primary")

    gate = asyncio.Event()

    async def hit() -> None:
        async with sm.serving("primary"):
            await gate.wait()

    tasks = [asyncio.create_task(hit()) for _ in range(5)]
    # Let every task enter the context.
    for _ in range(20):
        if sm.in_flight_count("primary") == 5:
            break
        await asyncio.sleep(0)
    assert sm.in_flight_count("primary") == 5
    assert (await sm.status("primary")).state == SlotState.SERVING

    gate.set()
    await asyncio.gather(*tasks)
    assert sm.in_flight_count("primary") == 0
    assert (await sm.status("primary")).state == SlotState.READY


async def test_serving_from_idle_returns_to_ready(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """A request that lands on an IDLE slot wakes it to SERVING → READY."""
    sm = SlotManager()
    await sm.load("primary")
    # Force IDLE manually.
    await sm._transition("primary", SlotState.IDLE)
    assert (await sm.status("primary")).state == SlotState.IDLE

    async with sm.serving("primary"):
        assert (await sm.status("primary")).state == SlotState.SERVING

    # After the request the slot is READY again — the dispatcher path
    # always rewarms, so falling back to IDLE is the monitor's job.
    assert (await sm.status("primary")).state == SlotState.READY


# ── IDLE monitor ────────────────────────────────────────────────────────────


async def test_idle_monitor_demotes_ready_to_idle(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """READY slots past the idle window flip to IDLE on the next sweep."""
    sm = SlotManager(idle_after_s=0.05, idle_monitor_interval_s=0.02)
    await sm.load("primary")
    # Make sure last_used is older than idle_after_s.
    sm._last_used["primary"] = 0.0  # epoch — definitely > 0.05s ago
    await sm.start_idle_monitor()
    try:
        # Poll for the transition.
        for _ in range(50):
            if (await sm.status("primary")).state == SlotState.IDLE:
                break
            await asyncio.sleep(0.02)
        assert (await sm.status("primary")).state == SlotState.IDLE
    finally:
        await sm.stop_idle_monitor()


async def test_idle_monitor_skips_serving_slots(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """An in-flight request must not be demoted to IDLE under the sweeper."""
    sm = SlotManager(idle_after_s=0.05, idle_monitor_interval_s=0.02)
    await sm.load("primary")
    await sm.start_idle_monitor()
    try:
        async with sm.serving("primary"):
            sm._last_used["primary"] = 0.0  # ancient timestamp
            # Wait a few sweep intervals — the slot must stay SERVING.
            for _ in range(5):
                await asyncio.sleep(0.03)
                assert (await sm.status("primary")).state == SlotState.SERVING
    finally:
        await sm.stop_idle_monitor()


async def test_serving_resets_idle_clock(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """serving() exit bumps last_used so the slot doesn't immediately re-idle."""
    sm = SlotManager(idle_after_s=10.0, idle_monitor_interval_s=10.0)
    await sm.load("primary")
    sm._last_used["primary"] = 0.0
    async with sm.serving("primary"):
        pass
    ts = sm.last_used("primary")
    assert ts is not None and ts > 0.1, "serving() must bump last_used on exit"
