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


class TestPlannedUnloads:
    """#1511: the dry-run preview names exactly the slots converge will sweep."""

    async def test_lists_running_slots_the_stack_does_not_name(self) -> None:
        sm = RecordingSlotManager(
            [
                FakeSnap("agent", SlotState.READY, "ace-saber"),
                FakeSnap("img", SlotState.READY, "flux"),
                FakeSnap("coder", SlotState.READY, "qwen"),
                FakeSnap("utility", SlotState.OFFLINE, None),
            ]
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        assert await _engine(sm).planned_unloads(stack) == ["img", "coder"]

    async def test_preview_matches_what_converge_unloads(self) -> None:
        snaps = [
            FakeSnap("agent", SlotState.READY, "ace-saber"),
            FakeSnap("embed", SlotState.READY, "bge-m3"),
            FakeSnap("img", SlotState.READY, "flux"),
            FakeSnap("stt", SlotState.READY, "whisper"),
            FakeSnap("tts", SlotState.STARTING, "kokoro"),
            FakeSnap("utility", SlotState.OFFLINE, None),
        ]
        stack = StackConfig(
            name="S",
            slots=[
                StackSlotEntry(slot="agent", model="ace-saber"),
                StackSlotEntry(
                    slot="embed",
                    capabilities=[
                        StackCapabilityRow(
                            child="embed", device="npu", provider="flm", model="bge-m3"
                        ),
                        # Disabled rows are not kept running — the sweep takes them.
                        StackCapabilityRow(
                            child="stt",
                            device="npu",
                            provider="flm",
                            model="whisper",
                            enabled=False,
                        ),
                    ],
                ),
            ],
        )
        preview = await _engine(RecordingSlotManager(snaps)).planned_unloads(stack)
        report = await _engine(RecordingSlotManager(snaps)).converge(stack)
        assert preview == report.unloaded == ["img", "stt"]

    async def test_no_slot_manager_previews_nothing(self) -> None:
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        assert await StackApplyEngine().planned_unloads(stack) == []

    async def test_preview_never_unloads_anything(self) -> None:
        sm = RecordingSlotManager([FakeSnap("img", SlotState.READY, "flux")])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        await _engine(sm).planned_unloads(stack)
        assert [c[0] for c in sm.calls] == ["list"]
