"""The GTT-leak signature: the pure rule, the holder set, and the manager seam.

A halogen engine that dies badly can leave tens of GiB of GPU memory held with
no process behind it (halogen-flash-server issue #79); the only fix is a host
reboot, and a start on top of it can hang at "reserving the KV pool".
``gtt_leak_message`` names that suspicion.

It is a WARNING, never a refusal: the GTT and KFD counters are host-wide while
hal0's slot list is one guest's, so a sibling guest's Vulkan slot on a shared
GPU looks exactly like a leak. The load path logs it, stores it on the slot's
state for the dashboard and goes on loading.

The rule is deliberately narrow. Vulkan slots hold their GTT through the DRM
render node and never appear under ``/sys/class/kfd/kfd/proc``, so "GPU memory
in use and no KFD process" describes every healthy Vulkan-only box too. The
rule therefore also requires that no slot of this box can hold the memory
(Review Focus 3).
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.hardware.gpu_view import GPUMemorySample
from hal0.slots.gtt_guard import (
    GFX1151,
    GTT_LEAK_THRESHOLD_MB,
    gtt_leak_message,
    slot_may_hold_gpu_memory,
)
from hal0.slots.manager import Slot, SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

GIB = 1024.0  # MiB per GiB: every figure here is MiB


def _message(**over: Any) -> str | None:
    args: dict[str, Any] = {
        "gtt_used_mb": 40 * GIB,
        "resident_gpu_mb": 0.0,
        "resident_gpu_slots": 0,
        "kfd_procs": 0,
    }
    args.update(over)
    return gtt_leak_message(**args)


# ── constants ──────────────────────────────────────────────────────────────


def test_threshold_is_eight_gib() -> None:
    assert GTT_LEAK_THRESHOLD_MB == 8 * 1024


def test_gfx1151_is_the_kfd_target_version_for_strix_halo() -> None:
    assert GFX1151 == 110501


# ── gtt_leak_message ───────────────────────────────────────────────────────


_MESSAGE_40_GIB = (
    "40 GiB of GPU memory is held that no slot on this box accounts for and no process "
    "holds the GPU: either another guest on this host holds it, or a previous GPU process "
    "did not give it back (halogen-flash-server issue #79). If no other guest runs GPU "
    "work, a host reboot frees it; a start on top of a real leak can hang at 'reserving "
    "the KV pool'."
)


def test_unexplained_gtt_with_no_residents_is_a_leak() -> None:
    assert _message() == _MESSAGE_40_GIB


def test_vulkan_residents_never_read_as_a_leak() -> None:
    """Review Focus 3: two Vulkan residents hold 38 GiB of a 40 GiB GTT; Vulkan
    never shows under the KFD proc table, so kfd_procs is 0 on a healthy box."""
    assert _message(resident_gpu_mb=38 * GIB, resident_gpu_slots=2, kfd_procs=0) is None


def test_a_resident_slot_with_no_size_estimate_still_clears_the_box() -> None:
    """A resident row whose footprint could not be sized (0 MiB) is still a
    holder of unknown size, so it must never be read as 'nothing holds this'."""
    assert _message(resident_gpu_mb=0.0, resident_gpu_slots=1) is None


def test_small_unexplained_gtt_is_not_a_leak() -> None:
    assert _message(gtt_used_mb=6 * GIB) is None


def test_the_threshold_is_exclusive() -> None:
    assert _message(gtt_used_mb=GTT_LEAK_THRESHOLD_MB) is None
    assert _message(gtt_used_mb=GTT_LEAK_THRESHOLD_MB + 1) is not None


def test_resident_memory_is_subtracted_before_the_threshold() -> None:
    assert _message(gtt_used_mb=20 * GIB, resident_gpu_mb=14 * GIB) is None


def test_a_live_kfd_process_explains_the_memory() -> None:
    assert _message(kfd_procs=1) is None


@pytest.mark.parametrize(
    "unknown",
    [
        {"gtt_used_mb": None},
        {"kfd_procs": None},
        {"resident_gpu_mb": None},
        {"resident_gpu_slots": None},
    ],
)
def test_any_unknown_input_is_never_a_leak(unknown: dict[str, None]) -> None:
    assert _message(**unknown) is None


def test_the_size_is_not_rounded_to_hide_a_fraction() -> None:
    msg = _message(gtt_used_mb=8.5 * GIB)
    assert msg is not None and msg.startswith("8.5 GiB of GPU memory")


# ── slot_may_hold_gpu_memory ───────────────────────────────────────────────


@pytest.mark.parametrize("state", ["starting", "warming", "ready", "serving", "idle", "unloading"])
def test_a_gpu_slot_in_a_holding_state_may_hold_memory(state: str) -> None:
    """``unloading`` counts: the stop is issued but the process may still be up."""
    assert slot_may_hold_gpu_memory({"name": "a", "state": state, "backend": "vulkan"})


@pytest.mark.parametrize("state", ["offline", "pulling", ""])
def test_a_slot_that_is_not_up_holds_nothing(state: str) -> None:
    assert not slot_may_hold_gpu_memory({"name": "a", "state": state, "backend": "vulkan"})


def test_every_real_slot_state_is_decided_on_purpose() -> None:
    """A new ``SlotState`` must be classified here, not fall through by accident."""
    decided = {"starting", "warming", "ready", "serving", "idle", "unloading"}
    decided |= {"offline", "pulling", "error"}
    assert {st.value for st in SlotState} == decided


def test_a_crashed_slot_in_error_does_not_shield_the_box() -> None:
    """A crashed halogen in ``error`` with its process gone is the leak case: it
    must not count as a holder, or the leak could never be named."""
    assert not slot_may_hold_gpu_memory({"name": "a", "state": "error", "backend": "rocm"})


def test_an_error_slot_whose_unit_is_still_active_holds_memory() -> None:
    assert slot_may_hold_gpu_memory(
        {"name": "a", "state": "error", "backend": "rocm"}, unit_active=True
    )


def test_an_error_slot_with_an_abandoned_teardown_may_still_hold_memory() -> None:
    """``slow_stop`` is hal0 having stopped WAITING on a stop that may yet finish."""
    assert slot_may_hold_gpu_memory(
        {"name": "a", "state": "error", "backend": "rocm", "metadata": {"slow_stop": "tok"}}
    )


@pytest.mark.parametrize("backend", ["cpu", "flm", "npu"])
def test_cpu_and_npu_slots_hold_no_gtt(backend: str) -> None:
    """An FLM slot is resident on the NPU; counting it would hide a real leak on
    exactly the boxes that run one."""
    assert not slot_may_hold_gpu_memory({"name": "a", "state": "ready", "backend": backend})


def test_an_flm_provider_holds_no_gtt_even_with_a_gpu_backend_token() -> None:
    assert not slot_may_hold_gpu_memory(
        {"name": "a", "state": "ready", "backend": "rocm", "metadata": {"provider": "flm"}}
    )


def test_an_unknown_backend_counts_as_a_holder() -> None:
    """Unprovable reads as 'may hold': the warning fires on a clear signature only."""
    assert slot_may_hold_gpu_memory({"name": "a", "state": "ready"})


def test_slot_objects_and_api_dicts_are_judged_alike() -> None:
    slot = Slot("a", state=SlotState.READY, backend="rocm")
    assert slot_may_hold_gpu_memory(slot)
    assert not slot_may_hold_gpu_memory(Slot("b", state=SlotState.READY, backend="flm"))


def test_holding_states_track_what_capacity_counts() -> None:
    """The rule's idea of 'holds GPU memory' and ``build_per_slot(include_starting=True)``
    must not drift apart: a state one counts and the other ignores is a false verdict."""
    from hal0.slots import capacity, gtt_guard

    assert (
        capacity._RESIDENT_STATES | {capacity._STARTING_STATE, "unloading"}
        == gtt_guard._HOLDING_STATES
    )


# ── no refusal type ────────────────────────────────────────────────────────


def test_there_is_no_refusal_error_for_a_suspected_leak() -> None:
    """The signature can never be certain on a shared host, so nothing raises."""
    from hal0.slots import state

    assert not hasattr(state, "SlotGttLeak")
    assert "SlotGttLeak" not in state.__all__


# ── SlotManager._guard_gtt_leak ────────────────────────────────────────────


def _sample(gtt_used_mb: float | None) -> GPUMemorySample:
    return GPUMemorySample(
        vendor="amd",
        is_uma=True,
        vram_total_mb=512.0,
        gtt_total_mb=98304.0 if gtt_used_mb is not None else None,
        total_mb=98304.0,
        vram_used_mb=100.0,
        gtt_used_mb=gtt_used_mb,
        used_mb=gtt_used_mb,
        gpu_busy=0.0,
        util_is_forced_high=False,
    )


class _Guarded:
    """A provider that asks hal0 for the GTT-leak check."""

    name = "guarded"
    gtt_leak_guard = True


class _Plain:
    name = "plain"


@pytest.fixture
def manager(slot_root: Path, container_stub: FakeContainerProvider) -> SlotManager:
    return SlotManager()


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    manager: SlotManager,
    *,
    gtt_used_mb: float | None,
    slots: list[Slot],
    rows: dict[str, dict[str, Any]] | None = None,
    kfd_procs: int | None = 0,
    real_build: bool = False,
    unit_active: bool | None = None,
) -> SimpleNamespace:
    """Patch the guard's readers; return the mocks so a test can inspect them."""
    sample = MagicMock(return_value=_sample(gtt_used_mb))
    kfd = MagicMock(return_value=kfd_procs)
    build = AsyncMock(return_value=rows or {})
    active = AsyncMock(return_value=bool(unit_active))
    monkeypatch.setattr("hal0.hardware.gpu_view.sample", sample)
    monkeypatch.setattr("hal0.providers._gpu.kfd_proc_count", kfd)
    if real_build:
        monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", lambda: True)
    else:
        monkeypatch.setattr("hal0.slots.capacity.build_per_slot", build)
    monkeypatch.setattr(manager, "list", AsyncMock(return_value=slots))
    if unit_active is not None:
        # Only when a test is about the unit probe: patching it unconditionally
        # would make every loaded slot read as stopped in ``load()`` tests.
        monkeypatch.setattr(manager, "_is_active", active)
    return SimpleNamespace(sample=sample, kfd=kfd, build=build, active=active)


async def test_guard_returns_the_warning_for_unexplained_gtt_with_nothing_resident(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    assert await manager._guard_gtt_leak("flash", _Guarded()) == _MESSAGE_40_GIB


async def test_guard_logs_the_suspicion_at_warning_with_the_slot_and_the_size(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    with caplog.at_level(logging.WARNING, logger="hal0.slots.manager"):
        await manager._guard_gtt_leak("flash", _Guarded())
    (record,) = [r for r in caplog.records if "slot.gtt_leak_suspected" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert record.slot == "flash"  # type: ignore[attr-defined]
    assert record.unexplained_gib == 40.0  # type: ignore[attr-defined]
    assert "another guest on this host" in record.getMessage()


async def test_guard_passes_two_vulkan_residents_holding_the_gtt(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 3, end to end: 38 of 40 GiB held by two Vulkan residents and
    kfd_procs 0 is a healthy box."""
    residents = [
        Slot("brain", state=SlotState.READY, backend="vulkan", model_id="m1"),
        Slot("coder", state=SlotState.READY, backend="vulkan", model_id="m2"),
    ]
    rows = {
        "brain": {"vram_mb": 19 * GIB, "ram_mb": 0.0, "mem_mb": 19 * GIB, "state": "ready"},
        "coder": {"vram_mb": 19 * GIB, "ram_mb": 0.0, "mem_mb": 19 * GIB, "state": "ready"},
    }
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=residents, rows=rows)
    with caplog.at_level(logging.WARNING, logger="hal0.slots.manager"):
        assert await manager._guard_gtt_leak("flash", _Guarded()) is None
    mocks.kfd.assert_called_once()
    assert not [r for r in caplog.records if "slot.gtt_leak_suspected" in r.getMessage()]


async def test_a_resident_slot_with_no_model_still_clears_the_box(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``build_per_slot`` skips a slot with no model bound, so counting its ROWS
    would read 'nothing resident'. The holder count is what the rule needs, and
    this goes through the REAL ``build_per_slot``."""
    modelless = Slot("brain", state=SlotState.READY, backend="vulkan", model_id=None)
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[modelless], real_build=True)
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_guard_asks_for_starting_slots_and_leaves_out_the_slot_being_loaded(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot still loading already holds its weights, so it counts as a holder;
    the slot under load must not vouch for its own GTT."""
    loading = Slot("flash", state=SlotState.STARTING, backend="rocm", model_id="f")
    sibling = Slot("brain", state=SlotState.STARTING, backend="vulkan", model_id="b")
    mocks = _wire(
        monkeypatch,
        manager,
        gtt_used_mb=40 * GIB,
        slots=[loading, sibling],
        rows={"brain": {"vram_mb": 20 * GIB, "state": "starting"}},
    )
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None
    (passed,) = mocks.build.await_args.args
    assert [s.name for s in passed] == ["brain"]
    assert mocks.build.await_args.kwargs["include_starting"] is True


async def test_an_unloading_slot_may_still_hold_the_memory(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    unloading = Slot("brain", state=SlotState.UNLOADING, backend="vulkan", model_id="b")
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[unloading])
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_a_crashed_slot_in_error_does_not_hide_the_leak(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crashed halogen IS the leak case: ERROR with its process gone."""
    crashed = Slot("flash2", state=SlotState.ERROR, backend="rocm", model_id="f")
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[crashed], unit_active=False)
    assert await manager._guard_gtt_leak("flash", _Guarded()) == _MESSAGE_40_GIB
    mocks.active.assert_awaited_once_with("flash2")


async def test_an_error_slot_whose_unit_is_still_active_holds_the_memory(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    errored = Slot("flash2", state=SlotState.ERROR, backend="rocm", model_id="f")
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[errored], unit_active=True)
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_an_error_slot_with_an_abandoned_teardown_holds_the_memory(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    stuck = Slot(
        "flash2",
        state=SlotState.ERROR,
        backend="rocm",
        model_id="f",
        metadata={"slow_stop": "tok"},
    )
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[stuck], unit_active=False)
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_the_unit_probe_is_made_only_for_a_slot_in_error(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = Slot("brain", state=SlotState.READY, backend="vulkan", model_id="b")
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[ready], unit_active=False)
    await manager._guard_gtt_leak("flash", _Guarded())
    mocks.active.assert_not_awaited()


async def test_an_npu_resident_does_not_hide_a_leak(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    flm = Slot("flm", state=SlotState.READY, backend="flm", model_id="qwen3")
    cpu = Slot("kokoro", state=SlotState.READY, backend="cpu", model_id="k")
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[flm, cpu])
    assert await manager._guard_gtt_leak("flash", _Guarded()) == _MESSAGE_40_GIB
    assert mocks.build.await_args.args[0] == []


async def test_guard_does_nothing_for_a_provider_that_does_not_ask(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    assert await manager._guard_gtt_leak("chat", _Plain()) is None
    assert await manager._guard_gtt_leak("chat", None) is None
    mocks.sample.assert_not_called()
    mocks.kfd.assert_not_called()


async def test_a_live_kfd_process_clears_the_box(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[], kfd_procs=1)
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_an_unreadable_kfd_table_is_never_a_suspicion(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[], kfd_procs=None)
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_a_box_with_no_gtt_counter_is_never_a_suspicion(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=None, slots=[])
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


async def test_a_reader_that_blows_up_never_raises(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing reader is not evidence of anything, and the guard never raises."""
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    mocks.sample.side_effect = RuntimeError("sysfs exploded")
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None

    mocks.sample.side_effect = None
    mocks.build.side_effect = RuntimeError("cgroup exploded")
    assert await manager._guard_gtt_leak("flash", _Guarded()) is None


# ── load() wiring ──────────────────────────────────────────────────────────


@pytest.fixture
def guarded_load(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """``load("chat")`` with a provider that asks for the check, a leak signature on
    the host, and every step of the load recorded in order."""
    events: list[str] = []
    seen: dict[str, Any] = {}
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Guarded())

    real_guard = manager._guard_gtt_leak

    async def guard(slot_name: str, provider: Any) -> str | None:
        events.append("guard")
        return await real_guard(slot_name, provider)

    @contextlib.asynccontextmanager
    async def admit(*_a: Any, **_k: Any):  # type: ignore[no-untyped-def]
        events.append("admit")
        yield

    real_spawn = manager._spawn_locked

    async def spawn(slot_name: str, *args: Any, **kwargs: Any) -> None:
        events.append("spawn")
        snapshot = await manager.status(slot_name)
        seen["message"] = snapshot.metadata.get("message")
        seen["extra"] = dict(manager._states[manager._key(slot_name)].extra)
        await real_spawn(slot_name, *args, **kwargs)

    monkeypatch.setattr(manager, "_guard_gtt_leak", guard)
    monkeypatch.setattr("hal0.slots.manager._preload_admit", admit)
    monkeypatch.setattr(manager, "_spawn_locked", spawn)
    return SimpleNamespace(events=events, seen=seen, stub=container_stub)


async def test_load_checks_before_pre_load_eviction_and_before_the_spawn(
    manager: SlotManager, guarded_load: SimpleNamespace
) -> None:
    """Eviction stops other slots to make room; sampling after it would read the
    memory they are still releasing as a leak."""
    await manager.load("chat")
    assert guarded_load.events == ["guard", "admit", "spawn"]


async def test_load_with_a_suspicion_warns_and_still_loads(
    manager: SlotManager, guarded_load: SimpleNamespace
) -> None:
    snap = await manager.load("chat")
    assert snap.state == SlotState.READY
    assert [cfg["name"] for cfg, _ in guarded_load.stub.load_calls] == ["chat"]


async def test_the_warning_is_on_the_slot_state_and_its_message_while_it_loads(
    manager: SlotManager, guarded_load: SimpleNamespace
) -> None:
    await manager.load("chat")
    assert guarded_load.seen["extra"]["gtt_leak_warning"] == _MESSAGE_40_GIB
    assert guarded_load.seen["message"] == _MESSAGE_40_GIB


async def test_the_warning_retires_when_the_slot_comes_up(
    manager: SlotManager, guarded_load: SimpleNamespace
) -> None:
    snap = await manager.load("chat")
    assert "gtt_leak_warning" not in manager._states[manager._key("chat")].extra
    assert "gtt_leak_warning" not in snap.metadata
    assert snap.metadata.get("message") != _MESSAGE_40_GIB


async def test_load_for_a_provider_that_does_not_ask_stores_nothing(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Plain())
    snap = await manager.load("chat")
    assert snap.state == SlotState.READY
    assert "gtt_leak_warning" not in manager._states[manager._key("chat")].extra
    mocks.sample.assert_not_called()


async def test_spawn_no_longer_runs_the_check(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned: ``_spawn_locked`` runs AFTER pre-load eviction, so the check lives in ``load``."""
    guard = AsyncMock(return_value=_MESSAGE_40_GIB)
    monkeypatch.setattr(manager, "_guard_gtt_leak", guard)
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Guarded())
    await manager._spawn_locked("flash", {"name": "flash", "port": 8099}, "m", model_info={})
    guard.assert_not_awaited()
    assert [cfg["name"] for cfg, _ in container_stub.load_calls] == ["flash"]


# ── the warning on the slot's state ────────────────────────────────────────


async def test_a_message_already_set_wins_over_the_warning_and_the_text_stays_in_metadata(
    manager: SlotManager,
) -> None:
    await manager._transition(
        "chat",
        SlotState.STARTING,
        message="pulling the runner image",
        extra={"gtt_leak_warning": _MESSAGE_40_GIB},
    )
    snap = await manager.status("chat")
    assert snap.metadata["message"] == "pulling the runner image"
    assert snap.metadata["gtt_leak_warning"] == _MESSAGE_40_GIB


async def test_the_warning_carries_through_warming_and_clears_on_ready(
    manager: SlotManager,
) -> None:
    await manager._transition(
        "chat", SlotState.STARTING, extra={"gtt_leak_warning": _MESSAGE_40_GIB}
    )
    await manager._transition("chat", SlotState.WARMING)
    assert (await manager.status("chat")).metadata["message"] == _MESSAGE_40_GIB
    await manager._transition("chat", SlotState.READY)
    snap = await manager.status("chat")
    assert "gtt_leak_warning" not in snap.metadata


async def test_the_warning_clears_when_the_slot_goes_down(manager: SlotManager) -> None:
    await manager._transition(
        "chat", SlotState.STARTING, extra={"gtt_leak_warning": _MESSAGE_40_GIB}
    )
    await manager._transition("chat", SlotState.ERROR, message="health timed out")
    assert manager._states[manager._key("chat")].extra["gtt_leak_warning"] == _MESSAGE_40_GIB
    await manager._transition("chat", SlotState.OFFLINE, force=True)
    assert "gtt_leak_warning" not in manager._states[manager._key("chat")].extra


async def test_a_retried_load_that_finds_nothing_retires_the_old_warning(
    manager: SlotManager,
) -> None:
    await manager._transition(
        "chat", SlotState.STARTING, extra={"gtt_leak_warning": _MESSAGE_40_GIB}
    )
    await manager._transition("chat", SlotState.ERROR, message="health timed out")
    await manager._transition("chat", SlotState.STARTING, extra={"gtt_leak_warning": None})
    assert "gtt_leak_warning" not in manager._states[manager._key("chat")].extra
