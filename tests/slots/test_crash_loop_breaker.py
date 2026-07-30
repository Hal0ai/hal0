"""Crash-loop breaker: load() from ERROR is throttled, parked, and resettable.

Issue i4: the "utility" slot flapped error→starting→error ~1/s because every
background inference request re-ran the FULL load from ERROR — no failure
counter, no backoff, no park state. ``load()`` now gates ERROR retries behind
an exponential backoff (SlotCrashLooping, 503 + Retry-After, NO transition),
parks the slot after ``_CRASH_LOOP_PARK_AFTER`` consecutive failures, and the
breaker resets on operator intent (manual load/restart/config edit) and on
any healthy convergence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots.manager import _CRASH_LOOP_PARK_AFTER, SlotManager
from hal0.slots.state import SlotCrashLooping, SlotState
from tests.slots.conftest import FakeContainerProvider


async def _fail_load_once(sm: SlotManager, container_stub: FakeContainerProvider) -> None:
    """One failed spawn → slot stamped ERROR, failure counted."""
    container_stub.fail_load = RuntimeError("spawn boom")
    with pytest.raises(RuntimeError):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR


def _rewind_backoff(sm: SlotManager) -> None:
    """Move the last-failure timestamp far into the past so the window is open."""
    key = sm._key("chat")
    count, ts = sm._load_failures[key]
    sm._load_failures[key] = (count, ts - 10_000.0)


async def test_load_from_error_inside_backoff_raises_without_spawning(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A lazy-load retry inside the backoff window is refused cheaply:
    no transition, no terminate, no systemctl — just SlotCrashLooping."""
    sm = SlotManager()
    await _fail_load_once(sm, container_stub)
    unloads_before = len(container_stub.unload_calls)

    with pytest.raises(SlotCrashLooping) as exc_info:
        await sm.load("chat")

    # Retryable 503 with a Retry-After hint for well-behaved clients.
    assert exc_info.value.status == 503
    assert exc_info.value.details["retry_after_s"] >= 1
    # No transition happened — state stayed ERROR, nothing hit the provider.
    assert sm._current_state("chat") == SlotState.ERROR
    assert len(container_stub.unload_calls) == unloads_before
    assert container_stub.load_calls == []


async def test_load_from_error_after_backoff_clears_wedge_and_recovers(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An allowed retry terminates the wedged unit first (clearing systemd's
    start-limit state) and, with the fault fixed, converges to READY."""
    sm = SlotManager()
    await _fail_load_once(sm, container_stub)
    _rewind_backoff(sm)

    container_stub.fail_load = None
    await sm.load("chat")

    assert sm._current_state("chat") == SlotState.READY
    assert container_stub.unload_calls, "retry from ERROR must terminate the wedged unit"
    # Success clears the breaker and its carried extras.
    key = sm._key("chat")
    assert key not in sm._load_failures
    rec = sm._states[key]
    assert "parked" not in rec.extra
    assert "load_failures" not in rec.extra


async def test_parks_after_max_consecutive_failures(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """After N consecutive failures the slot parks: load() short-circuits
    with SlotCrashLooping regardless of elapsed time."""
    sm = SlotManager()
    for _ in range(_CRASH_LOOP_PARK_AFTER):
        await _fail_load_once(sm, container_stub)
        _rewind_backoff(sm)

    key = sm._key("chat")
    rec = sm._states[key]
    assert rec.extra["parked"] is True
    assert rec.extra["load_failures"] == _CRASH_LOOP_PARK_AFTER

    unloads_before = len(container_stub.unload_calls)
    with pytest.raises(SlotCrashLooping) as exc_info:
        await sm.load("chat")
    assert exc_info.value.details["parked"] is True
    # Parked refusal is free — no terminate, no spawn.
    assert len(container_stub.unload_calls) == unloads_before
    assert container_stub.load_calls == []


async def test_manual_reset_unparks_and_load_recovers(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """reset_load_failures (the operator surface) clears the parked breaker;
    the next load runs for real and a healthy convergence drops the flag."""
    sm = SlotManager()
    for _ in range(_CRASH_LOOP_PARK_AFTER):
        await _fail_load_once(sm, container_stub)
        _rewind_backoff(sm)

    sm.reset_load_failures("chat")
    container_stub.fail_load = None
    await sm.load("chat")

    key = sm._key("chat")
    assert sm._current_state("chat") == SlotState.READY
    assert key not in sm._load_failures
    assert "parked" not in sm._states[key].extra


async def test_restart_resets_breaker_and_recovers(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """restart() is operator intent — a parked slot restarts without tripping
    the gate (#1224 semantics preserved)."""
    sm = SlotManager()
    for _ in range(_CRASH_LOOP_PARK_AFTER):
        await _fail_load_once(sm, container_stub)
        _rewind_backoff(sm)

    container_stub.fail_load = None
    await sm.restart("chat")

    assert sm._current_state("chat") == SlotState.READY


async def test_update_config_resets_breaker(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A config edit resets the breaker — the operator fixed the cause."""
    sm = SlotManager()
    await _fail_load_once(sm, container_stub)
    assert sm._load_failures

    await sm.update_config("chat", {"port": 8082})

    assert sm._key("chat") not in sm._load_failures


# ── slot.state flap coalescing (defense in depth) ────────────────────────────


class _BusStub:
    """Captures EventBus.emit calls (type, severity, source, message, data)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, str, dict[str, Any] | None]] = []

    async def emit(
        self,
        type_: str,
        severity: str,
        source: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((type_, severity, source, message, data))


def _pairs(bus: _BusStub) -> list[tuple[str, str]]:
    return [(e[4]["from"], e[4]["to"]) for e in bus.events if e[0] == "slot.state" and e[4]]


async def test_repeated_flap_pairs_coalesce_within_window(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Identical ERROR-edged transition pairs inside the window fold into a
    single emitted event; the next emit after the window carries the count."""
    bus = _BusStub()
    sm = SlotManager(event_bus=bus)

    await _fail_load_once(sm, container_stub)
    _rewind_backoff(sm)
    await _fail_load_once(sm, container_stub)  # second full flap
    _rewind_backoff(sm)

    # starting→error emitted exactly once — the repeat was folded.
    assert _pairs(bus).count(("starting", "error")) == 1

    # Expire the coalescing window: the next flap emits again, carrying the
    # fold count so operators retain evidence of the suppressed repeats.
    key = sm._key("chat")
    last_emit, folded = sm._flap_emits[(key, "starting→error")]
    assert folded == 1
    sm._flap_emits[(key, "starting→error")] = (last_emit - 10_000.0, folded)

    await _fail_load_once(sm, container_stub)

    assert _pairs(bus).count(("starting", "error")) == 2
    repeated = [
        e[4]
        for e in bus.events
        if e[0] == "slot.state" and e[4] and e[4].get("from") == "starting" and "repeats" in e[4]
    ]
    assert repeated and repeated[-1]["repeats"] == 1


async def test_non_error_transitions_are_never_coalesced(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Healthy lifecycle pairs (offline→starting, warming→ready, …) always emit."""
    bus = _BusStub()
    sm = SlotManager(event_bus=bus)

    await sm.load("chat")
    await sm.unload("chat")
    await sm.load("chat")

    assert _pairs(bus).count(("offline", "starting")) == 2
    assert _pairs(bus).count(("warming", "ready")) == 2


async def test_backoff_gate_emits_no_events(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A throttled retry produces zero events — no starting, no error rows."""
    bus = _BusStub()
    sm = SlotManager(event_bus=bus)
    await _fail_load_once(sm, container_stub)
    n_events = len(bus.events)

    for _ in range(10):
        with pytest.raises(SlotCrashLooping):
            await sm.load("chat")

    assert len(bus.events) == n_events
