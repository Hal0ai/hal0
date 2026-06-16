"""Tests for SlotManager's push-driven failure detector.

When a slot's container unit goes inactive while the slot is in a live
state (READY / SERVING / IDLE), the manager must flip state and emit
the SSE frame within ~1s — not on the next ``status()`` poll.

These tests monkeypatch the fail-watch poll interval down to a few
hundred milliseconds so they finish quickly while still exercising the
real ``_fail_watch_loop`` and ``_update_fail_watcher`` codepaths.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


@pytest.fixture
def fast_fail_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tighten the fail-watch poll interval so tests run in <5s."""
    monkeypatch.setattr(mgr_mod, "_FAIL_WATCH_INTERVAL_S", 0.2)


async def _wait_for_state(
    sm: SlotManager,
    name: str,
    target: SlotState,
    *,
    timeout_s: float = 5.0,
) -> SlotState:
    """Poll the manager's in-memory state until ``target`` or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        rec = sm._states.get(name)
        if rec is not None and rec.state == target:
            return rec.state
        await asyncio.sleep(0.05)
    rec = sm._states.get(name)
    return rec.state if rec is not None else SlotState.OFFLINE


async def test_fail_watcher_pushes_offline_when_unit_stops(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """The unit going inactive while the slot is READY transitions to OFFLINE.

    Units stop legitimately out-of-band (GPU arbiter handoff, systemd
    stop, OOM-kill with Restart= pending). From the slot's perspective
    this is a clean not-loaded state — the next inference request
    reloads it. Reflected as OFFLINE (grey dot) per the dot-state spec,
    reserving ERROR (red dot, "investigate") for real
    spawn/health/load failures.
    """
    sm = SlotManager()
    snap = await sm.load("chat")
    assert snap.state == SlotState.READY
    # The watcher should be alive and tracked.
    assert "chat" in sm._fail_watchers
    assert not sm._fail_watchers["chat"].done()

    # Simulate the unit stopping out-of-band. No call to status() —
    # the push-driven watcher is what should react.
    container_stub.active.clear()

    observed = await _wait_for_state(sm, "chat", SlotState.OFFLINE, timeout_s=5.0)
    assert observed == SlotState.OFFLINE, (
        f"watcher failed to push OFFLINE within 5s; final state={observed}"
    )

    rec = sm._states["chat"]
    assert "stopped" in rec.message.lower() or "auto-reload" in rec.message.lower(), (
        f"OFFLINE record should explain the stopped unit (got {rec.message!r})"
    )
    # Watcher is one-shot — it should have exited after firing.
    watcher = sm._fail_watchers.get("chat")
    if watcher is not None:
        for _ in range(20):
            if watcher.done():
                break
            await asyncio.sleep(0.05)
        assert watcher.done()


async def test_fail_watcher_emits_sse_frame_for_pushed_eviction(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """The watcher-triggered OFFLINE transition must broadcast to SSE subscribers."""
    sm = SlotManager()
    await sm.load("chat")

    # Subscribe before the failure so we observe the watcher-emitted frame.
    stream = sm.state_stream()
    # Trigger failure.
    container_stub.active.clear()

    async def _next_offline() -> str:
        async for rec in stream:
            if rec.state == SlotState.OFFLINE:
                return rec.message
        return ""

    try:
        msg = await asyncio.wait_for(_next_offline(), timeout=5.0)
    except TimeoutError:
        pytest.fail("watcher did not emit an OFFLINE SSE frame within 5s")
    assert "stopped" in msg.lower() or "auto-reload" in msg.lower()


async def test_fail_watcher_does_not_fire_when_slot_unloads_cleanly(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """A clean unload() must cancel the watcher; no spurious ERROR push."""
    sm = SlotManager()
    await sm.load("chat")
    assert "chat" in sm._fail_watchers
    await sm.unload("chat")
    # Watcher must be gone (or done) after the slot left live-state.
    watcher = sm._fail_watchers.get("chat")
    assert watcher is None or watcher.done()
    # Give any stray watcher time to misbehave; then assert OFFLINE held.
    await asyncio.sleep(0.6)  # > _FAIL_WATCH_INTERVAL_S
    assert sm._states["chat"].state == SlotState.OFFLINE


async def test_fail_watcher_demotes_to_error_when_health_fails(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """#783/B4: a ready slot whose unit stays active but whose /health probe
    starts failing (model server crashed / wedged) is demoted to ERROR.

    Previously the watcher only checked ``is_active`` — a crashed-but-active
    container kept publishing as dispatchable READY, so /api/health/system
    and hal0_slot_up both lied. Demoting to ERROR makes the health endpoint
    report degraded and drops the slot from the dispatchable set. The probe
    result is recorded as health_ok=False for the metric fold-in (#791).
    """
    sm = SlotManager()
    await sm.load("chat")
    assert "chat" in sm._fail_watchers
    # Unit stays active, but the model server stops answering /health.
    container_stub.healthy = False
    observed = await _wait_for_state(sm, "chat", SlotState.ERROR, timeout_s=5.0)
    assert observed == SlotState.ERROR
    rec = sm._states["chat"]
    assert rec.extra.get("health_ok") is False


async def test_fail_watcher_keeps_ready_while_health_ok(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """Guard: a healthy active slot must NOT be demoted by the watcher."""
    sm = SlotManager()
    await sm.load("chat")
    assert "chat" in sm._fail_watchers
    # Let several poll intervals elapse with the unit active + healthy.
    await asyncio.sleep(0.8)  # > 3 * _FAIL_WATCH_INTERVAL_S (0.2)
    assert sm._states["chat"].state == SlotState.READY
