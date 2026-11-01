"""The GTT-leak guard: the pure rule, its error type, and the manager seam.

A halogen engine that dies badly can leave tens of GiB of GPU memory held with
no process behind it (halogen-flash-server issue #79); the only fix is a host
reboot, and the next load fails deep inside a weight allocation instead of
saying so. ``gtt_leak_message`` names that state before the unit is written.

The rule is deliberately narrow. Vulkan slots hold their GTT through the DRM
render node and never appear under ``/sys/class/kfd/kfd/proc``, so "GPU memory
in use and no KFD process" describes every healthy Vulkan-only box too. The
rule therefore also requires that no resident slot can account for the memory
(Review Focus 3).
"""

from __future__ import annotations

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
from hal0.slots.state import SlotGttLeak, SlotSpawnFailed, SlotState
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


def test_unexplained_gtt_with_no_residents_is_a_leak() -> None:
    msg = _message()
    assert msg == (
        "40 GiB of GPU memory is held that no hal0 slot accounts for and no process "
        "holds the GPU: memory a previous GPU process did not give back "
        "(halogen-flash-server issue #79). A host reboot is required before this "
        "slot can start."
    )


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


@pytest.mark.parametrize("state", ["starting", "warming", "ready", "serving", "idle"])
def test_a_gpu_slot_in_a_holding_state_may_hold_memory(state: str) -> None:
    assert slot_may_hold_gpu_memory({"name": "a", "state": state, "backend": "vulkan"})


@pytest.mark.parametrize("state", ["offline", "pulling", "error", "stopping", ""])
def test_a_slot_that_is_not_up_holds_nothing(state: str) -> None:
    assert not slot_may_hold_gpu_memory({"name": "a", "state": state, "backend": "vulkan"})


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
    """Unprovable reads as 'may hold': the guard only ever refuses on proof."""
    assert slot_may_hold_gpu_memory({"name": "a", "state": "ready"})


def test_slot_objects_and_api_dicts_are_judged_alike() -> None:
    slot = Slot("a", state=SlotState.READY, backend="rocm")
    assert slot_may_hold_gpu_memory(slot)
    assert not slot_may_hold_gpu_memory(Slot("b", state=SlotState.READY, backend="flm"))


def test_holding_states_track_what_capacity_counts() -> None:
    """The rule's idea of 'holds GPU memory' and ``build_per_slot(include_starting=True)``
    must not drift apart: a state one counts and the other ignores is a false verdict."""
    from hal0.slots import capacity, gtt_guard

    assert capacity._RESIDENT_STATES | {capacity._STARTING_STATE} == gtt_guard._HOLDING_STATES


# ── SlotGttLeak ────────────────────────────────────────────────────────────


def test_slot_gtt_leak_is_a_spawn_failure_with_its_own_code() -> None:
    err = SlotGttLeak("held")
    assert isinstance(err, SlotSpawnFailed)
    assert err.code == "slot.gtt_leak"


def test_slot_gtt_leak_is_exported() -> None:
    from hal0.slots import state

    assert "SlotGttLeak" in state.__all__


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
    """A provider that asks hal0 for the GTT-leak guard."""

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
) -> SimpleNamespace:
    """Patch the guard's three readers; return the mocks so a test can inspect them."""
    sample = MagicMock(return_value=_sample(gtt_used_mb))
    kfd = MagicMock(return_value=kfd_procs)
    build = AsyncMock(return_value=rows or {})
    monkeypatch.setattr("hal0.hardware.gpu_view.sample", sample)
    monkeypatch.setattr("hal0.providers._gpu.kfd_proc_count", kfd)
    monkeypatch.setattr("hal0.slots.capacity.build_per_slot", build)
    monkeypatch.setattr(manager, "list", AsyncMock(return_value=slots))
    return SimpleNamespace(sample=sample, kfd=kfd, build=build)


async def test_guard_refuses_unexplained_gtt_with_nothing_resident(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    with pytest.raises(SlotGttLeak) as exc_info:
        await manager._guard_gtt_leak("flash", _Guarded())
    err = exc_info.value
    assert err.code == "slot.gtt_leak"
    assert str(err).startswith("40 GiB of GPU memory is held that no hal0 slot accounts for")
    assert "A host reboot is required before this slot can start." in str(err)
    assert err.details["slot"] == "flash"


async def test_guard_passes_two_vulkan_residents_holding_the_gtt(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
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
    await manager._guard_gtt_leak("flash", _Guarded())
    mocks.kfd.assert_called_once()


async def test_guard_asks_for_starting_slots_and_leaves_out_the_slot_being_loaded(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot still loading already holds its weights, so it counts as a holder;
    the slot under load is in STARTING itself and must not vouch for its own GTT."""
    loading = Slot("flash", state=SlotState.STARTING, backend="rocm", model_id="f")
    sibling = Slot("brain", state=SlotState.STARTING, backend="vulkan", model_id="b")
    mocks = _wire(
        monkeypatch,
        manager,
        gtt_used_mb=40 * GIB,
        slots=[loading, sibling],
        rows={"brain": {"vram_mb": 20 * GIB, "state": "starting"}},
    )
    await manager._guard_gtt_leak("flash", _Guarded())
    (passed,) = mocks.build.await_args.args
    assert [s.name for s in passed] == ["brain"]
    assert mocks.build.await_args.kwargs["include_starting"] is True


async def test_an_npu_resident_does_not_hide_a_leak(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    flm = Slot("flm", state=SlotState.READY, backend="flm", model_id="qwen3")
    cpu = Slot("kokoro", state=SlotState.READY, backend="cpu", model_id="k")
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[flm, cpu])
    with pytest.raises(SlotGttLeak):
        await manager._guard_gtt_leak("flash", _Guarded())
    assert mocks.build.await_args.args[0] == []


async def test_guard_does_nothing_for_a_provider_that_does_not_ask(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    await manager._guard_gtt_leak("chat", _Plain())
    await manager._guard_gtt_leak("chat", None)
    mocks.sample.assert_not_called()
    mocks.kfd.assert_not_called()


async def test_a_live_kfd_process_clears_the_box(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[], kfd_procs=1)
    await manager._guard_gtt_leak("flash", _Guarded())


async def test_an_unreadable_kfd_table_never_blocks_a_load(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[], kfd_procs=None)
    await manager._guard_gtt_leak("flash", _Guarded())


async def test_a_box_with_no_gtt_counter_never_blocks_a_load(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=None, slots=[])
    await manager._guard_gtt_leak("flash", _Guarded())


async def test_a_reader_that_blows_up_never_blocks_a_load(
    manager: SlotManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard refuses on proof only: a failing reader is not proof of a leak."""
    mocks = _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    mocks.sample.side_effect = RuntimeError("sysfs exploded")
    await manager._guard_gtt_leak("flash", _Guarded())

    mocks.sample.side_effect = None
    mocks.build.side_effect = RuntimeError("cgroup exploded")
    await manager._guard_gtt_leak("flash", _Guarded())


# ── _spawn_locked wiring ───────────────────────────────────────────────────


async def test_spawn_runs_the_guard_before_load_sync_and_a_leak_writes_no_unit(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=40 * GIB, slots=[])
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Guarded())
    with pytest.raises(SlotGttLeak):
        await manager._spawn_locked("flash", {"name": "flash", "port": 8099}, "m", model_info={})
    assert container_stub.load_calls == []


async def test_spawn_loads_when_the_guard_finds_no_leak(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, manager, gtt_used_mb=2 * GIB, slots=[])
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Guarded())
    await manager._spawn_locked("flash", {"name": "flash", "port": 8099}, "m", model_info={})
    assert [cfg["name"] for cfg, _ in container_stub.load_calls] == ["flash"]


async def test_spawn_does_not_call_the_guard_for_a_provider_that_does_not_ask(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = AsyncMock()
    monkeypatch.setattr(manager, "_guard_gtt_leak", guard)
    monkeypatch.setattr("hal0.providers.container._spec_provider_for", lambda _cfg: _Plain())
    await manager._spawn_locked("chat", {"name": "chat", "port": 8081}, "m", model_info={})
    guard.assert_not_awaited()
    assert len(container_stub.load_calls) == 1


async def test_spawn_survives_a_provider_that_cannot_be_resolved(
    manager: SlotManager,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution failures belong to ``load_sync``'s own error path, not the guard's."""

    def boom(_cfg: dict[str, Any]) -> None:
        raise RuntimeError("unknown runtime family")

    monkeypatch.setattr("hal0.providers.container._spec_provider_for", boom)
    await manager._spawn_locked("chat", {"name": "chat", "port": 8081}, "m", model_info={})
    assert len(container_stub.load_calls) == 1
