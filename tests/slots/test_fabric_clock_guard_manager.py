"""The fabric-clock guard's manager seam: refuse/warn/off and unreadable-never-refuses.

The pure rule lives in :mod:`hal0.slots.fabric_clock_guard`
(tests/slots/test_fabric_clock_guard.py); this file covers
``SlotManager._guard_fabric_clock`` — the policy switch
(``[slots].fabric_clock_guard`` = refuse/warn/off) and how it reads the
candidate slot list through ``manager.list()``, mirroring
tests/slots/test_gtt_guard.py's manager-seam tests.
"""

from __future__ import annotations

import pytest

from hal0.slots.fabric_clock_guard import FabricClockState
from hal0.slots.manager import Slot, SlotManager
from hal0.slots.state import SlotFabricClockHazard, SlotState

pytestmark = pytest.mark.usefixtures("slot_root", "container_stub")

_HELD = FabricClockState(
    device="/sys/class/drm/card1/device", perf_level="high", held=True, readable=True
)
_NOT_HELD = FabricClockState(
    device="/sys/class/drm/card1/device", perf_level="auto", held=False, readable=True
)
_UNREADABLE = FabricClockState(device=None, perf_level=None, held=False, readable=False)


def _manager(policy: str = "refuse") -> SlotManager:
    return SlotManager(fabric_clock_guard=policy)


async def _wire(monkeypatch: pytest.MonkeyPatch, manager: SlotManager, *, others, state) -> None:
    async def listing(*, exclude: str | None = None) -> list[Slot]:
        return [s for s in others if s.name != exclude]

    async def read_state() -> FabricClockState:
        return state

    monkeypatch.setattr(manager, "list", listing)
    monkeypatch.setattr("hal0.slots.fabric_clock_guard.read_fabric_clock", lambda *a, **k: state)


async def test_refuses_npu_load_beside_up_gpu_slot_when_unheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.READY, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_NOT_HELD)
    with pytest.raises(SlotFabricClockHazard) as exc:
        await manager._guard_fabric_clock("flm", {"device": "npu"})
    assert "flm" in str(exc.value) or "chat" in str(exc.value)


async def test_refuses_gpu_load_beside_up_npu_slot_when_unheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager("refuse")
    npu = Slot("flm", state=SlotState.READY, backend="flm", model_id="m1")
    await _wire(monkeypatch, manager, others=[npu], state=_NOT_HELD)
    with pytest.raises(SlotFabricClockHazard):
        await manager._guard_fabric_clock("chat", {"device": "gpu"})


async def test_passes_when_held(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.READY, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_HELD)
    assert await manager._guard_fabric_clock("flm", {"device": "npu"}) is None


async def test_passes_when_no_gpu_counterpart_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.OFFLINE, backend="vulkan", model_id=None)
    await _wire(monkeypatch, manager, others=[gpu], state=_NOT_HELD)
    assert await manager._guard_fabric_clock("flm", {"device": "npu"}) is None


async def test_warn_policy_returns_message_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    manager = _manager("warn")
    gpu = Slot("chat", state=SlotState.SERVING, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_NOT_HELD)
    with caplog.at_level(logging.WARNING, logger="hal0.slots.manager"):
        message = await manager._guard_fabric_clock("flm", {"device": "npu"})
    assert message is not None and "flm" in message
    assert [r for r in caplog.records if "slot.fabric_clock_unheld" in r.getMessage()]


async def test_off_policy_never_reads_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("off")

    def boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("fabric clock should not be read when policy is off")

    monkeypatch.setattr("hal0.slots.fabric_clock_guard.read_fabric_clock", boom)
    assert await manager._guard_fabric_clock("flm", {"device": "npu"}) is None


async def test_unreadable_state_warns_even_under_refuse_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.READY, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_UNREADABLE)
    message = await manager._guard_fabric_clock("flm", {"device": "npu"})
    assert message is not None and "could not be read" in message


async def test_env_override_wins_over_configured_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_FABRIC_CLOCK_GUARD", "off")
    manager = SlotManager(fabric_clock_guard="refuse")
    assert manager._fabric_clock_guard == "off"


# ── review P1-1: cross-slot admission lock for concurrent cold loads ─────────


async def test_concurrent_cold_loads_admit_exactly_one_counterpart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent cold loads (one NPU, one GPU) must not both pass.

    Before the admission lock + `_fabric_pending` reservation, each load's
    `list()` scan ran before either had published STARTING, so both saw "no
    counterpart yet" and both proceeded — the exact hazard this guard
    exists to prevent. `list()` here returns a fixed empty snapshot (no one
    is OFFLINE→STARTING yet from `list()`'s point of view) so the only thing
    that can catch the race is the reservation set.
    """
    import asyncio

    manager = _manager("refuse")
    await _wire(manager=manager, monkeypatch=monkeypatch, others=[], state=_NOT_HELD)

    results: dict[str, object] = {}

    async def attempt(name: str, cfg: dict) -> None:
        try:
            results[name] = await manager._guard_fabric_clock(name, cfg)
        except SlotFabricClockHazard as exc:
            results[name] = exc

    await asyncio.gather(
        attempt("flm", {"device": "npu"}),
        attempt("chat", {"device": "gpu"}),
    )
    outcomes = list(results.values())
    refused = [o for o in outcomes if isinstance(o, SlotFabricClockHazard)]
    admitted = [o for o in outcomes if o is None]
    assert len(refused) == 1, f"expected exactly one refusal, got {outcomes!r}"
    assert len(admitted) == 1, f"expected exactly one admission, got {outcomes!r}"


# ── review P1-2: a live ERROR counterpart is not offline ─────────────────────


async def test_live_error_counterpart_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.ERROR, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_NOT_HELD)
    monkeypatch.setattr(manager, "_is_active", lambda name: _true())
    with pytest.raises(SlotFabricClockHazard):
        await manager._guard_fabric_clock("flm", {"device": "npu"})


async def test_dead_error_counterpart_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("refuse")
    gpu = Slot("chat", state=SlotState.ERROR, backend="vulkan", model_id="m1")
    await _wire(monkeypatch, manager, others=[gpu], state=_NOT_HELD)
    monkeypatch.setattr(manager, "_is_active", lambda name: _false())
    assert await manager._guard_fabric_clock("flm", {"device": "npu"}) is None


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


# ── review P2-4: CUDA is not the iGPU this guard protects ────────────────────


async def test_cuda_incoming_load_not_refused_beside_npu(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager("refuse")
    npu = Slot("flm", state=SlotState.READY, backend="flm", model_id="m1")
    await _wire(monkeypatch, manager, others=[npu], state=_NOT_HELD)
    assert await manager._guard_fabric_clock("chat", {"device": "gpu-cuda"}) is None


# ── review P2-3: a real refusal restores state and skips failure bookkeeping ──


async def test_real_load_refusal_restores_offline_without_failure_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
    slot_root,
) -> None:
    """A real `SlotManager.load()` call (not a direct `_guard_fabric_clock`
    call) must: raise `SlotFabricClockHazard`, leave the refused slot
    OFFLINE (not ERROR), and record no load failure — refusing a pairing is
    not the same as the slot itself being broken, so it must not feed
    crash-loop backoff/park bookkeeping.
    """
    from pathlib import Path

    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    (root / "npu.toml").write_text(
        "\n".join(
            [
                'name = "npu"',
                "port = 8088",
                'device = "npu"',
                'runtime = "container"',
                'profile = "flm"',
                "[model]",
                'default = "gemma3:4b"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manager = _manager("refuse")
    chat = Slot("chat", state=SlotState.READY, backend="vulkan", model_id="m1")

    async def listing(*, exclude: str | None = None) -> list[Slot]:
        return [s for s in (chat,) if s.name != exclude]

    monkeypatch.setattr(manager, "list", listing)
    monkeypatch.setattr(
        "hal0.slots.fabric_clock_guard.read_fabric_clock", lambda *a, **k: _NOT_HELD
    )

    with pytest.raises(SlotFabricClockHazard):
        await manager.load("npu", "gemma3:4b")

    state_value = manager.state("npu")
    assert state_value == SlotState.OFFLINE, state_value
    rec = manager._states.get(manager._key("npu"))
    load_failures = (rec.extra or {}).get("load_failures") if rec else None
    assert not load_failures, rec.extra if rec else None
