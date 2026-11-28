"""#4082: ``load()`` must not tear down a running slot that adoption parked in WARMING.

A serving slot whose /health failed for a moment was marked ERROR, adopted
again by the next status call (to WARMING, because /health still said no), and
then stopped and restarted by the next request's lazy ``load()``, which read
WARMING as the residue of a load that never converged (#1791). Its in-flight
requests failed.

The two cases are told apart by a mark only adoption stamps. An adopted
WARMING slot with an active unit is left running: ``load()`` promotes it when
/health answers and otherwise refuses, retryably. A WARMING slot left by a
load that timed out is still torn down and re-entered, and the crash-loop
breaker still guards it.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots import watchdog as wd_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotCrashLooping, SlotNotReady, SlotState
from hal0.upstreams.registry import UpstreamRegistry

from .conftest import FakeContainerProvider


@pytest.fixture(autouse=True)
def _slow_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the background promotion poll out of these tests: load() is what
    # is under test, and the poll would race it to READY.
    monkeypatch.setattr(mgr_mod, "_ADOPTED_WARMING_POLL_INTERVAL_S", 3600.0)


async def _adopt_into_warming(sm: SlotManager, stub: FakeContainerProvider) -> None:
    stub.active.add("chat")
    stub.healthy = False
    snap = await sm.status("chat")
    assert snap.state is SlotState.WARMING
    assert sm._states[sm._key("chat")].extra.get("adopted_warming") is True


def _time_out_readiness(stub: FakeContainerProvider) -> None:
    async def never_ready(port: int) -> None:
        raise TimeoutError("health wait exceeded")

    stub.wait_ready = never_ready  # type: ignore[method-assign]


async def test_load_does_not_terminate_an_adopted_warming_slot(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)

    with pytest.raises(SlotNotReady) as excinfo:
        await sm.load("chat")

    assert container_stub.unload_calls == [], "the running engine must not be stopped"
    assert container_stub.load_calls == [], "and nothing is re-spawned"
    assert "chat" in container_stub.active
    assert sm._current_state("chat") is SlotState.WARMING
    assert excinfo.value.status == 503
    assert excinfo.value.details["retry_after_s"] >= 1
    assert "hal0 slot restart chat" in excinfo.value.details["restart"]
    # The promotion poll adoption armed is still the slot's way to READY.
    assert sm._key("chat") in sm._adopt_promoters


async def test_load_promotes_an_adopted_warming_slot_that_answers(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    await _adopt_into_warming(sm, container_stub)

    container_stub.healthy = True
    slot = await sm.load("chat")

    assert slot.state is SlotState.READY
    assert container_stub.unload_calls == []
    assert container_stub.load_calls == []
    assert reg.get("chat") is not None, "promotion registers the upstream, as a load does"
    assert "adopted_warming" not in sm._states[sm._key("chat")].extra


async def test_a_warming_slot_left_by_a_timed_out_load_is_still_torn_down(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    """The #1791 case is unchanged: not adopted, so it is residue."""
    sm = SlotManager()
    _time_out_readiness(container_stub)
    await sm.load("chat")
    assert sm._current_state("chat") is SlotState.WARMING
    assert "adopted_warming" not in sm._states[sm._key("chat")].extra

    container_stub.wait_ready = FakeContainerProvider.wait_ready.__get__(  # type: ignore[method-assign]
        container_stub, FakeContainerProvider
    )
    container_stub.unload_calls.clear()
    container_stub.load_calls.clear()

    slot = await sm.load("chat")

    assert slot.state is SlotState.READY
    assert container_stub.unload_calls, "stale WARMING is torn down first"
    assert container_stub.load_calls, "and the unit re-spawned"


async def test_the_crash_loop_breaker_still_guards_the_timed_out_case(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    import time

    sm = SlotManager()
    _time_out_readiness(container_stub)
    await sm.load("chat")
    sm._load_failures[sm._key("chat")] = (3, time.monotonic())

    with pytest.raises(SlotCrashLooping):
        await sm.load("chat")


async def test_an_earlier_adoption_does_not_protect_a_later_failed_load(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    """``adopted`` stays on the record for good; the WARMING mark does not.
    A slot adopted once, then unloaded and loaded into a timeout, is residue."""
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)
    await sm.unload("chat")
    assert "adopted_warming" not in sm._states[sm._key("chat")].extra

    _time_out_readiness(container_stub)
    await sm.load("chat")
    rec = sm._states[sm._key("chat")]
    assert rec.state is SlotState.WARMING
    assert rec.extra.get("adopted") is True  # carried, and not what load() reads
    assert "adopted_warming" not in rec.extra

    container_stub.wait_ready = FakeContainerProvider.wait_ready.__get__(  # type: ignore[method-assign]
        container_stub, FakeContainerProvider
    )
    container_stub.unload_calls.clear()
    slot = await sm.load("chat")
    assert slot.state is SlotState.READY
    assert container_stub.unload_calls


async def test_an_adopted_warming_slot_whose_unit_died_is_torn_down(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    """Only a RUNNING engine is protected. With the unit gone there is nothing
    to keep, and the load re-enters the lifecycle."""
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)
    container_stub.active.discard("chat")
    container_stub.healthy = True

    slot = await sm.load("chat")

    assert slot.state is SlotState.READY
    assert container_stub.load_calls, "the unit was spawned again"


async def test_a_load_that_names_another_model_is_still_a_swap(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    """Protection is for waking what is running. An explicit load of a
    different model is the caller asking for a teardown."""
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)
    running = sm._states[sm._key("chat")].model_id
    container_stub.healthy = True

    # The stub may not know the model; the teardown is what is asserted.
    with contextlib.suppress(Exception):
        await sm.load("chat", model_id=f"{running}-other")

    assert container_stub.unload_calls, "a swap tears the running unit down"


async def test_a_load_that_names_the_running_model_is_a_wake(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)
    running = sm._states[sm._key("chat")].model_id

    with pytest.raises(SlotNotReady):
        await sm.load("chat", model_id=running)
    assert container_stub.unload_calls == []


async def test_the_journal_lines_name_the_slot_and_the_reason(
    slot_root: Path, container_stub: FakeContainerProvider, caplog: pytest.LogCaptureFixture
) -> None:
    """hal0-api's console handler prints only the message, so the message
    itself must carry the slot, the state it came from and why."""
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    with caplog.at_level(logging.INFO, logger="hal0.slots.manager"):
        await _adopt_into_warming(sm, container_stub)
        with pytest.raises(SlotNotReady):
            await sm.load("chat")
        await sm._transition("chat", SlotState.ERROR, message="probe failed", force=True)
    messages = [r.getMessage() for r in caplog.records]

    adopted = next(m for m in messages if m.startswith("slot.adopted "))
    assert "slot=chat" in adopted and "resolved=warming" in adopted
    left = next(m for m in messages if m.startswith("slot.load_left_adopted_warming "))
    assert "slot=chat" in left and "reason=unit_active_health_not_ok" in left
    transition = next(m for m in messages if m.startswith("slot.transition "))
    assert "slot=chat" in transition and "to=" in transition
    error = next(m for m in messages if m.startswith("slot.error "))
    assert "slot=chat" in error and "from=warming" in error and "probe failed" in error
    # ``extra`` is unchanged for structured consumers.
    rec = next(r for r in caplog.records if r.getMessage().startswith("slot.error "))
    assert rec.slot == "chat" and rec.reason == "probe failed"


async def test_a_unit_that_stops_by_itself_is_named_in_the_journal(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine killed from outside (an out-of-memory killer, a crash) must
    leave a line that names the slot and says hal0 did not stop it, with the
    unit's exit status when systemd has one."""
    import asyncio

    monkeypatch.setattr(wd_mod, "_FAIL_WATCH_INTERVAL_S", 0.05)
    container_stub.unit_status = lambda _cfg: {  # type: ignore[attr-defined]
        "Result": "exit-code",
        "ExecMainStatus": "137",
    }
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await sm.load("chat")
    assert sm._current_state("chat") is SlotState.READY

    with caplog.at_level(logging.INFO, logger="hal0.slots"):
        container_stub.active.discard("chat")  # the engine dies; hal0 stopped nothing
        for _ in range(100):
            if sm._current_state("chat") is SlotState.OFFLINE:
                break
            await asyncio.sleep(0.02)
    assert sm._current_state("chat") is SlotState.OFFLINE
    assert container_stub.unload_calls == []

    line = next(
        r.getMessage() for r in caplog.records if r.getMessage().startswith("slot.unit_exited ")
    )
    assert "slot=chat" in line
    assert "state=ready" in line
    assert "stopped_by=not_hal0" in line
    assert "exit_status=137" in line and "result=exit-code" in line
