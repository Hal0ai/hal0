"""Tests for SlotManager's push-driven failure detector.

When a slot's model drops out of lemond's ``/v1/health.loaded[]`` while
the slot is in a live state (READY / SERVING / IDLE), the manager
must flip to ERROR and emit the SSE frame within ~1s — not on the next
``status()`` poll.

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


async def test_fail_watcher_pushes_offline_when_model_drops_from_lemond(
    slot_root: Any,
    lemonade_loaded_stub: dict[str, Any],
    fast_fail_watch: None,
) -> None:
    """Model leaving lemond's loaded[] while slot is READY transitions to OFFLINE.

    Lemonade routinely evicts loaded models (idle-TTL, nuclear-evict on
    a sibling load failure, max_models pressure). From the slot's
    perspective this is a clean unload — the next inference request
    hot-reloads the model. Reflected as OFFLINE (grey dot) per the
    dot-state spec, reserving ERROR (red dot, "investigate") for real
    spawn/health/load failures.
    """
    sm = SlotManager()
    snap = await sm.load("primary")
    assert snap.state == SlotState.READY
    # The watcher should be alive and tracked.
    assert "primary" in sm._fail_watchers
    assert not sm._fail_watchers["primary"].done()

    # Simulate eviction: lemond no longer reports the model as loaded.
    # No call to status() — the push-driven watcher is what should react.
    lemonade_loaded_stub["loaded"] = []

    observed = await _wait_for_state(sm, "primary", SlotState.OFFLINE, timeout_s=5.0)
    assert observed == SlotState.OFFLINE, (
        f"watcher failed to push OFFLINE within 5s; final state={observed}"
    )

    rec = sm._states["primary"]
    assert "evict" in rec.message.lower() or "auto-reload" in rec.message.lower(), (
        f"OFFLINE record should carry an eviction explanation (got {rec.message!r})"
    )
    # Watcher is one-shot — it should have exited after firing.
    watcher = sm._fail_watchers.get("primary")
    if watcher is not None:
        for _ in range(20):
            if watcher.done():
                break
            await asyncio.sleep(0.05)
        assert watcher.done()


async def test_fail_watcher_emits_sse_frame_for_pushed_eviction(
    slot_root: Any,
    lemonade_loaded_stub: dict[str, Any],
    fast_fail_watch: None,
) -> None:
    """The watcher-triggered OFFLINE transition must broadcast to SSE subscribers."""
    sm = SlotManager()
    await sm.load("primary")

    # Subscribe before the failure so we observe the watcher-emitted frame.
    stream = sm.state_stream()
    # Trigger failure.
    lemonade_loaded_stub["loaded"] = []

    async def _next_offline() -> str:
        async for rec in stream:
            if rec.state == SlotState.OFFLINE:
                return rec.message
        return ""

    try:
        msg = await asyncio.wait_for(_next_offline(), timeout=5.0)
    except TimeoutError:
        pytest.fail("watcher did not emit an OFFLINE SSE frame within 5s")
    assert "evict" in msg.lower() or "auto-reload" in msg.lower()


async def test_fail_watcher_does_not_fire_when_slot_unloads_cleanly(
    slot_root: Any,
    lemonade_loaded_stub: dict[str, Any],
    fast_fail_watch: None,
) -> None:
    """A clean unload() must cancel the watcher; no spurious ERROR push."""
    sm = SlotManager()
    await sm.load("primary")
    assert "primary" in sm._fail_watchers
    await sm.unload("primary")
    # Watcher must be gone (or done) after the slot left live-state.
    watcher = sm._fail_watchers.get("primary")
    assert watcher is None or watcher.done()
    # Give any stray watcher time to misbehave; then assert OFFLINE held.
    await asyncio.sleep(0.6)  # > _FAIL_WATCH_INTERVAL_S
    assert sm._states["primary"].state == SlotState.OFFLINE
