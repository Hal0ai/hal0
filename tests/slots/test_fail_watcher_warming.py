"""WARMING is fail-watched — but only on unit liveness, never /health.

A health-wait timeout parks the slot in WARMING (``_await_ready``), which
was previously outside ``_FAIL_WATCH_LIVE_STATES`` — no watcher ran, so a
unit that later died left the slot lying in WARMING forever. WARMING is now
watched with softer semantics: /health failures never strike (a big model
legitimately loads for minutes), only ``_WARMING_INACTIVE_STRIKES``
consecutive is-active failures flip the slot to ERROR.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots.manager import _FAIL_WATCH_LIVE_STATES, SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


@pytest.fixture
def fast_fail_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mgr_mod, "_FAIL_WATCH_INTERVAL_S", 0.1)


def test_warming_is_in_the_watched_set() -> None:
    assert SlotState.WARMING in _FAIL_WATCH_LIVE_STATES


async def _load_into_warming(sm: SlotManager, container_stub: FakeContainerProvider) -> None:
    """Drive load() through a health-wait timeout so the slot parks WARMING."""

    async def _wait_ready_timeout(port: int, timeout_s: float | None = None) -> None:
        raise TimeoutError("health wait timed out")

    container_stub.wait_ready = _wait_ready_timeout  # type: ignore[method-assign]
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.WARMING


async def test_warming_slot_gets_a_watcher_and_survives_failing_health(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """Active unit + failing /health must NOT strike a WARMING slot."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)
    assert "chat" in sm._fail_watchers, "WARMING must spawn a fail-watcher"

    # Model server still loading: /health says not-ok while the unit is up.
    container_stub.healthy = False
    await asyncio.sleep(0.6)  # several poll intervals — plenty of strikes

    assert sm._current_state("chat") == SlotState.WARMING, (
        "a slow-loading model must never be demoted on /health while WARMING"
    )


async def test_warming_slot_flips_error_when_unit_dies(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """A stably-dead unit under a WARMING slot is caught (was the blind spot)."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)

    container_stub.active.discard("chat")

    deadline = asyncio.get_event_loop().time() + 5.0
    observed: Any = None
    while asyncio.get_event_loop().time() < deadline:
        observed = sm._current_state("chat")
        if observed == SlotState.ERROR:
            break
        await asyncio.sleep(0.05)
    assert observed == SlotState.ERROR, (
        f"dead unit under WARMING never surfaced; final state={observed}"
    )
    rec = sm._states["chat"]
    assert "warming" in (rec.message or "").lower()


async def test_warming_slot_tolerates_a_transient_inactive_blip(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """One or two inactive reads (unit still activating) must not strike out."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)

    # Blip: inactive for ~one poll, then back.
    container_stub.active.discard("chat")
    await asyncio.sleep(0.15)
    container_stub.active.add("chat")
    await asyncio.sleep(0.5)

    assert sm._current_state("chat") == SlotState.WARMING


def _spy_recovery(sm: SlotManager, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record calls to sm.unload / sm.load while delegating to the originals."""
    calls: list[tuple[str, str]] = []
    orig_unload = sm.unload
    orig_load = sm.load

    async def spy_unload(name: str) -> Any:
        calls.append(("unload", name))
        return await orig_unload(name)

    async def spy_load(name: str, model_id: str | None = None) -> Any:
        calls.append(("load", name))
        return await orig_load(name, model_id)

    monkeypatch.setattr(sm, "unload", spy_unload)
    monkeypatch.setattr(sm, "load", spy_load)
    return calls


async def test_warming_slot_with_fresh_timestamp_is_not_recovered(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A freshly-WARMING slot (active unit, /health still down) must NOT trip
    the staleness watchdog — a slow cold load below the threshold is left alone.
    """
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)
    calls = _spy_recovery(sm, monkeypatch)

    # Model server still loading: /health down, unit active, timestamp fresh.
    container_stub.healthy = False
    await asyncio.sleep(0.6)  # several poll intervals

    assert calls == [], f"a fresh WARMING slot must not be recovered by the watchdog; got {calls}"
    assert sm._current_state("chat") == SlotState.WARMING


async def test_warming_slot_recovers_when_stale(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WARMING slot stuck past _WARMING_STALE_AFTER_S (unit still active) is
    auto-recovered via unload → load, self-healing the wedged anchor.
    """
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)
    assert "chat" in sm._fail_watchers
    calls = _spy_recovery(sm, monkeypatch)

    # On the reload, let the model converge so recovery lands in READY.
    async def _wait_ok(port: int, timeout_s: float | None = None) -> None:
        return None

    container_stub.wait_ready = _wait_ok  # type: ignore[method-assign]

    # Age the slot past the staleness ceiling (unit stays active throughout).
    sm._states["chat"].updated_at = time.time() - mgr_mod._WARMING_STALE_AFTER_S - 1

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if ("unload", "chat") in calls and ("load", "chat") in calls:
            break
        await asyncio.sleep(0.05)

    assert ("unload", "chat") in calls, f"watchdog never unloaded wedged slot; {calls}"
    assert ("load", "chat") in calls, f"watchdog never reloaded wedged slot; {calls}"
    # unload must precede load (recovery order), and the reload converged.
    assert calls.index(("unload", "chat")) < calls.index(("load", "chat"))
    assert sm._current_state("chat") == SlotState.READY


async def _await_state(
    sm: SlotManager, name: str, target: SlotState, timeout_s: float = 5.0
) -> Any:
    """Poll until *name* reaches *target* or the deadline lapses."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    observed: Any = None
    while asyncio.get_event_loop().time() < deadline:
        observed = sm._current_state(name)
        if observed == target:
            return observed
        await asyncio.sleep(0.05)
    return observed


async def test_repeated_warming_error_cycles_recover_without_watcher_leak(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """A slot that keeps dying while WARMING must resolve to ERROR every cycle,
    re-arm cleanly on the next load, and never accumulate stale watchers.
    """
    sm = SlotManager()

    for cycle in range(3):
        await _load_into_warming(sm, container_stub)
        # Exactly one live watcher per slot — no accumulation across cycles.
        live = [t for t in sm._fail_watchers.values() if not t.done()]
        assert len(live) <= 1, f"cycle {cycle}: watcher leak ({len(live)} live)"

        # Unit dies while warming → the watcher strikes it to ERROR.
        container_stub.active.discard("chat")
        observed = await _await_state(sm, "chat", SlotState.ERROR)
        assert observed == SlotState.ERROR, f"cycle {cycle}: never reached ERROR ({observed})"

        # The watcher self-retired on its own ERROR transition.
        w = sm._fail_watchers.get("chat")
        assert w is None or w.done(), f"cycle {cycle}: watcher not retired after ERROR"


async def test_warming_stale_recovery_that_fails_lands_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """A wedged WARMING slot whose staleness-triggered recovery reload ALSO
    fails must settle in a clean ERROR — not silently re-wedge (the
    WARMING → recover → ERROR cycle).
    """
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)

    # Recovery reload can't spawn → load() stamps ERROR and re-raises; the
    # watchdog swallows the exception and returns, leaving the slot ERROR.
    container_stub.fail_load = RuntimeError("spawn failed on recovery")
    sm._states["chat"].updated_at = time.time() - mgr_mod._WARMING_STALE_AFTER_S - 1

    observed = await _await_state(sm, "chat", SlotState.ERROR)
    assert observed == SlotState.ERROR, f"failed recovery never surfaced ERROR ({observed})"
    # No orphaned live watcher after the terminal ERROR.
    w = sm._fail_watchers.get("chat")
    assert w is None or w.done()
