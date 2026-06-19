"""Tests for the three slot states wired in task #10.

Covers PLAN.md §5 state machine:

  - **PULLING**  — load() flips offline→pulling→starting when the model is
    not on disk yet, and skips pulling when it is.
  - **SERVING**  — SlotManager.serving() context flips READY/IDLE → SERVING
    on the first concurrent entry and back to READY on the last exit.
  - **IDLE**     — the background sweeper demotes READY → IDLE after the
    configured idle timeout, and serving() resets the clock.

All systemctl + health-probe calls are stubbed via the shared
``container_stub`` fixture in ``tests/slots/conftest.py`` so the suite
is hermetic.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

# ── PULLING ──────────────────────────────────────────────────────────────────


async def test_load_transitions_through_pulling_when_not_cached(
    slot_root: Path,
    container_stub: FakeContainerProvider,
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
    snap = await sm.load("chat")
    await asyncio.wait_for(task, timeout=2.0)

    assert snap.state == SlotState.READY
    assert pulls == ["qwen3-4b-q4_k_m"], "pull_runner must fire exactly once"
    # PULLING must appear, then STARTING, then WARMING, then READY.
    assert "pulling" in seen
    assert seen.index("pulling") < seen.index("starting") < seen.index("ready")


async def test_load_skips_pulling_when_model_cached(
    slot_root: Path,
    container_stub: FakeContainerProvider,
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
    snap = await sm.load("chat")
    await asyncio.wait_for(task, timeout=2.0)

    assert snap.state == SlotState.READY
    assert pulls == [], "cached model must not trigger pull_runner"
    assert "pulling" not in seen


async def test_load_without_pull_runner_never_enters_pulling(
    slot_root: Path,
    container_stub: FakeContainerProvider,
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
    await sm.load("chat")
    await asyncio.wait_for(task, timeout=2.0)
    assert "pulling" not in seen


async def test_pull_runner_failure_flips_to_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
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
    # The unit never started — the pull aborted first — so status()
    # can't adopt to READY (container_stub starts with no active units).

    with pytest.raises(PullBoom):
        await sm.load("chat")
    snap = await sm.status("chat")
    assert snap.state == SlotState.ERROR


# ── SERVING ─────────────────────────────────────────────────────────────────


async def test_serving_context_flips_ready_to_serving_and_back(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    assert (await sm.status("chat")).state == SlotState.READY

    async with sm.serving("chat"):
        assert (await sm.status("chat")).state == SlotState.SERVING
        assert sm.in_flight_count("chat") == 1

    assert (await sm.status("chat")).state == SlotState.READY
    assert sm.in_flight_count("chat") == 0


async def test_serving_concurrent_requests_keep_state_serving(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """N concurrent requests must NOT toggle READY↔SERVING mid-flight."""
    sm = SlotManager()
    await sm.load("chat")

    gate = asyncio.Event()

    async def hit() -> None:
        async with sm.serving("chat"):
            await gate.wait()

    tasks = [asyncio.create_task(hit()) for _ in range(5)]
    # Let every task enter the context.
    for _ in range(20):
        if sm.in_flight_count("chat") == 5:
            break
        await asyncio.sleep(0)
    assert sm.in_flight_count("chat") == 5
    assert (await sm.status("chat")).state == SlotState.SERVING

    gate.set()
    await asyncio.gather(*tasks)
    assert sm.in_flight_count("chat") == 0
    assert (await sm.status("chat")).state == SlotState.READY


async def test_serving_from_idle_returns_to_ready(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A request that lands on an IDLE slot wakes it to SERVING → READY."""
    sm = SlotManager()
    await sm.load("chat")
    # Force IDLE manually.
    await sm._transition("chat", SlotState.IDLE)
    assert (await sm.status("chat")).state == SlotState.IDLE

    async with sm.serving("chat"):
        assert (await sm.status("chat")).state == SlotState.SERVING

    # After the request the slot is READY again — the dispatcher path
    # always rewarms, so falling back to IDLE is the monitor's job.
    assert (await sm.status("chat")).state == SlotState.READY


# ── IDLE monitor ────────────────────────────────────────────────────────────


async def test_idle_monitor_demotes_ready_to_idle(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """READY slots past the idle window flip to IDLE on the next sweep."""
    sm = SlotManager(idle_after_s=0.05, idle_monitor_interval_s=0.02)
    await sm.load("chat")
    # Make sure last_used is older than idle_after_s.
    sm._last_used["chat"] = 0.0  # epoch — definitely > 0.05s ago
    await sm.start_idle_monitor()
    try:
        # Poll for the transition.
        for _ in range(50):
            if (await sm.status("chat")).state == SlotState.IDLE:
                break
            await asyncio.sleep(0.02)
        assert (await sm.status("chat")).state == SlotState.IDLE
    finally:
        await sm.stop_idle_monitor()


async def test_idle_monitor_skips_serving_slots(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An in-flight request must not be demoted to IDLE under the sweeper."""
    sm = SlotManager(idle_after_s=0.05, idle_monitor_interval_s=0.02)
    await sm.load("chat")
    await sm.start_idle_monitor()
    try:
        async with sm.serving("chat"):
            sm._last_used["chat"] = 0.0  # ancient timestamp
            # Wait a few sweep intervals — the slot must stay SERVING.
            for _ in range(5):
                await asyncio.sleep(0.03)
                assert (await sm.status("chat")).state == SlotState.SERVING
    finally:
        await sm.stop_idle_monitor()


async def test_serving_resets_idle_clock(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """serving() exit bumps last_used so the slot doesn't immediately re-idle."""
    sm = SlotManager(idle_after_s=10.0, idle_monitor_interval_s=10.0)
    await sm.load("chat")
    sm._last_used["chat"] = 0.0
    async with sm.serving("chat"):
        pass
    ts = sm.last_used("chat")
    assert ts is not None and ts > 0.1, "serving() must bump last_used on exit"


# ── IDLE eviction (#902) ─────────────────────────────────────────────────────


def _write_min_slot(
    root: Path,
    name: str,
    *,
    port: int,
    idle_timeout_s: int | None = None,
) -> None:
    """Write a minimal llama-server slot TOML, optionally pinning idle_timeout_s."""
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'backend = "vulkan"',
        'provider = "llama-server"',
        "enabled = true",
    ]
    if idle_timeout_s is not None:
        lines.append(f"idle_timeout_s = {idle_timeout_s}")
    lines += ["[model]", 'default = "qwen3-4b-q4_k_m"', ""]
    (root / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


async def test_idle_sweep_unloads_slot_past_ttl(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A non-pinned slot idle past its TTL is unloaded (RAM freed), not just relabeled."""
    _write_min_slot(slot_root, "rerank", port=8090)
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("rerank")
    assert (await sm.status("rerank")).state == SlotState.READY
    sm._last_used["rerank"] = 0.0  # ancient — well past the 0.01s TTL

    await sm._sweep_idle_once()

    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert any(c.get("name") == "rerank" for c in container_stub.unload_calls)


async def test_idle_sweep_pins_slot_with_zero_timeout(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """idle_timeout_s = 0 pins a slot — never TTL-evicted even when long idle."""
    _write_min_slot(slot_root, "rerank", port=8090, idle_timeout_s=0)
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("rerank")
    sm._last_used["rerank"] = 0.0  # ancient

    await sm._sweep_idle_once()

    # Demoted to IDLE (stage 1) but NOT unloaded (stage 2 skipped).
    assert (await sm.status("rerank")).state == SlotState.IDLE
    assert not container_stub.unload_calls


async def test_idle_sweep_does_not_evict_default_anchor(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """chat (a default-pinned anchor) relabels IDLE but is never evicted under default config."""
    # slot_root already provides chat.toml with no explicit idle_timeout_s.
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("chat")
    sm._last_used["chat"] = 0.0  # ancient

    await sm._sweep_idle_once()

    assert (await sm.status("chat")).state == SlotState.IDLE
    assert not container_stub.unload_calls


async def test_idle_sweep_never_evicts_serving_slot(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A slot with an in-flight request is never evicted mid-request."""
    _write_min_slot(slot_root, "rerank", port=8090)
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("rerank")
    async with sm.serving("rerank"):
        sm._last_used["rerank"] = 0.0  # ancient, but serving_count > 0
        await sm._sweep_idle_once()
        assert (await sm.status("rerank")).state == SlotState.SERVING
    assert not container_stub.unload_calls


async def test_explicit_positive_ttl_is_honored(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A per-slot idle_timeout_s overrides the global default in both directions."""
    _write_min_slot(slot_root, "rerank", port=8090, idle_timeout_s=2)
    # Global default is tiny, but the per-slot 2s override must win.
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("rerank")

    # Idle for ~1s: under the 2s TTL → still loaded.
    sm._last_used["rerank"] = time.time() - 1.0
    await sm._sweep_idle_once()
    assert (await sm.status("rerank")).state in (SlotState.READY, SlotState.IDLE)
    assert not container_stub.unload_calls

    # Idle for ~3s: past the 2s TTL → evicted.
    sm._last_used["rerank"] = time.time() - 3.0
    await sm._sweep_idle_once()
    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert any(c.get("name") == "rerank" for c in container_stub.unload_calls)


async def test_evicted_slot_wakes_on_next_request(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An evicted slot reloads transparently when used again (wake-on-request)."""
    _write_min_slot(slot_root, "rerank", port=8090)
    sm = SlotManager(idle_after_s=0.0, evict_after_s=0.01, idle_monitor_interval_s=10.0)
    await sm.load("rerank")
    sm._last_used["rerank"] = 0.0
    await sm._sweep_idle_once()
    assert (await sm.status("rerank")).state == SlotState.OFFLINE

    # Next request path (dispatcher wake-on-request uses start()/load()).
    woke = await sm.start("rerank")
    assert woke.state == SlotState.READY
    # A fresh container load happened after the eviction unload.
    assert sum(1 for c in container_stub.load_calls if c[0].get("name") == "rerank") == 2
