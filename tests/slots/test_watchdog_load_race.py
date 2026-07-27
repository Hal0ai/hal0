"""A fail-watcher must not clobber an in-flight load.

Live regression (105, v1.0.0-alpha.2): a large model's WARMING load holds the
per-slot lock for the whole cold-load window. The SlotWatchdog stamped ERROR
(``_transition(..., force=True)``) WITHOUT taking that lock — so while the unit
read briefly inactive during warmup it flipped the slot to ERROR mid-load. The
load's own final ``WARMING -> READY`` transition (non-force) then hit
``current == ERROR`` and raised ``IllegalSlotTransition`` — surfacing as a 409
on ``/api/slots/{name}/load`` and a slot wedged in ``error``. Small/fast slots
loaded before the watcher's strike window elapsed, so only big models failed.

The fix: the watchdog acquires the same per-slot lock and RE-VERIFIES the fault
before stamping, so it can never race a load — and a load that converged
healthy while the watcher waited for the lock is left READY.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import hal0.slots.watchdog as wd
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def test_load_survives_concurrent_fail_watch(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot whose unit reads inactive during warmup must still land READY
    when the model converges — the fail-watcher must not stamp ERROR onto the
    in-flight load."""
    # Tighten the watcher so its strike fires inside the test window.
    monkeypatch.setattr(wd, "_FAIL_WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(wd, "_WARMING_INACTIVE_STRIKES", 1)

    sm = SlotManager()

    # Model is "big": the unit stays inactive through warmup (so the watcher's
    # is-active probe strikes), and readiness blocks until we release the gate.
    gate = asyncio.Event()

    def _spawn_no_active(cfg: dict, model_info: dict) -> None:
        # Record the spawn but DON'T mark the unit active yet — mimics a
        # container that hasn't converged (reads inactive to the watcher).
        container_stub.load_calls.append((dict(cfg), dict(model_info)))

    async def _slow_wait_ready(port: int, timeout_s: float | None = None) -> None:
        await gate.wait()

    monkeypatch.setattr(container_stub, "load_sync", _spawn_no_active)
    monkeypatch.setattr(container_stub, "wait_ready", _slow_wait_ready)
    container_stub.healthy = False

    load_task = asyncio.create_task(sm.load("chat"))
    # Let load reach WARMING and the fail-watcher take several strike ticks
    # while the unit still reads inactive.
    await asyncio.sleep(0.1)

    # Model finishes: unit active + serving /health, readiness unblocks.
    container_stub.active.add("chat")
    container_stub.healthy = True
    gate.set()

    slot = await load_task

    assert slot.state == SlotState.READY
    assert sm._current_state("chat") == SlotState.READY
