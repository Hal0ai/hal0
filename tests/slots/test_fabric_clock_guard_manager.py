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
