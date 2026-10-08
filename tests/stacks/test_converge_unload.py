"""Tests for converge() declarative unload sweep.

Targeted file run:
    uv run pytest tests/stacks/test_converge_unload.py -q
"""

from __future__ import annotations

from hal0.config.schema import StackCapabilityRow, StackConfig, StackSlotEntry
from hal0.slots.state import SlotState
from hal0.stacks.apply import StackApplyEngine
from tests.stacks.conftest import FakeSnap, RecordingOrchestrator, RecordingSlotManager


def _engine(sm: RecordingSlotManager) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())


class TestUnloadSweep:
    async def test_running_slot_not_in_stack_is_unloaded(self) -> None:
        sm = RecordingSlotManager(
            [
                FakeSnap("agent", SlotState.READY, "ace-saber"),
                FakeSnap("img", SlotState.READY, "flux"),
            ]
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert ("unload", "img", None) in sm.calls
        assert report.unloaded == ["img"]

    async def test_stack_primary_slot_is_not_unloaded(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert report.unloaded == []
        assert not [c for c in sm.calls if c[0] == "unload"]

    async def test_enabled_capability_slot_is_not_unloaded(self) -> None:
        # embed system slot is running; stack enables embed → must NOT be swept.
        sm = RecordingSlotManager([FakeSnap("embed", SlotState.READY, "bge-m3")])
        stack = StackConfig(
            name="S",
            slots=[
                StackSlotEntry(
                    slot="embed",
                    capabilities=[
                        StackCapabilityRow(
                            child="embed", device="npu", provider="flm", model="bge-m3"
                        )
                    ],
                )
            ],
        )
        report = await _engine(sm).converge(stack)
        assert report.unloaded == []

    async def test_offline_slot_not_in_stack_is_left_alone(self) -> None:
        sm = RecordingSlotManager([FakeSnap("img", SlotState.OFFLINE, None)])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert not [c for c in sm.calls if c[0] == "unload"]
        assert report.unloaded == []

    async def test_unload_failure_is_recorded(self) -> None:
        class Boom(RecordingSlotManager):
            async def unload(self, slot_name):
                raise RuntimeError("stop failed")

        sm = Boom([FakeSnap("img", SlotState.READY, "flux")])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert report.errors == [("img", "stop failed")]
        assert report.unloaded == []
