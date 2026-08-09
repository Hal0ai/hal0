"""Tests for converge() primary-slot pass (load/swap/skip/transitional/error).

Targeted file run:
    cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_primary.py -q
"""

from __future__ import annotations

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.slots.state import SlotState
from hal0.stacks.apply import StackApplyEngine
from tests.stacks.conftest import FakeSnap, RecordingOrchestrator, RecordingSlotManager


def _engine(sm: RecordingSlotManager) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())


def _stack(*entries: StackSlotEntry) -> StackConfig:
    return StackConfig(name="S", slots=list(entries))


class TestPrimaryConverge:
    async def test_offline_slot_is_loaded(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.OFFLINE, None)])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("load", "agent", "ace-saber") in sm.calls
        assert report.loaded == ["agent"]

    async def test_missing_snapshot_is_loaded(self) -> None:
        sm = RecordingSlotManager([])  # agent not configured/known yet
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("load", "agent", "ace-saber") in sm.calls
        assert report.loaded == ["agent"]

    async def test_dispatchable_different_model_is_swapped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "old-model")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("swap", "agent", "ace-saber") in sm.calls
        assert report.swapped == ["agent"]
        assert not [c for c in sm.calls if c[0] == "load"]

    async def test_dispatchable_same_model_is_skipped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.skipped == ["agent"]
        assert not [c for c in sm.calls if c[0] in ("load", "swap")]

    async def test_transitional_slot_is_skipped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.WARMING, "ace-saber")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.skipped == ["agent"]
        assert not [c for c in sm.calls if c[0] in ("load", "swap")]

    async def test_same_model_changed_config_is_reloaded(self) -> None:
        # Profile-only (or flags-only) change: Phase A rewrote the slot file but
        # the model is unchanged — the running server must restart to pick up
        # the new config instead of silently keeping its old flags.
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        report = await _engine(sm).converge(
            _stack(StackSlotEntry(slot="agent", model="ace-saber")), changed_slots={"agent"}
        )
        assert ("restart", "agent", None) in sm.calls
        assert report.reloaded == ["agent"]
        assert report.skipped == []

    async def test_same_model_unchanged_config_is_still_skipped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        report = await _engine(sm).converge(
            _stack(StackSlotEntry(slot="agent", model="ace-saber")), changed_slots={"utility"}
        )
        assert report.skipped == ["agent"]
        assert not [c for c in sm.calls if c[0] in ("load", "swap", "restart")]

    async def test_changed_config_different_model_swaps_not_restarts(self) -> None:
        # A model change subsumes the config reload — swap already restarts the
        # container; a second restart would double-bounce the slot.
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "old-model")])
        report = await _engine(sm).converge(
            _stack(StackSlotEntry(slot="agent", model="ace-saber")), changed_slots={"agent"}
        )
        assert ("swap", "agent", "ace-saber") in sm.calls
        assert report.swapped == ["agent"]
        assert not [c for c in sm.calls if c[0] == "restart"]

    async def test_reload_failure_is_recorded_not_raised(self) -> None:
        class Boom(RecordingSlotManager):
            async def restart(self, slot_name):
                raise RuntimeError("restart failed")

        sm = Boom([FakeSnap("agent", SlotState.READY, "ace-saber")])
        report = await _engine(sm).converge(
            _stack(StackSlotEntry(slot="agent", model="ace-saber")), changed_slots={"agent"}
        )
        assert report.errors == [("agent", "restart failed")]
        assert report.reloaded == []

    async def test_entry_without_model_is_ignored(self) -> None:
        sm = RecordingSlotManager([])
        report = await _engine(sm).converge(
            _stack(StackSlotEntry(slot="stt"))
        )  # no model → capability-only
        assert report.loaded == [] and report.swapped == [] and report.skipped == []

    async def test_load_failure_is_recorded_not_raised(self) -> None:
        class Boom(RecordingSlotManager):
            async def load(self, slot_name, model_id=None):
                raise RuntimeError("spawn failed")

        sm = Boom([FakeSnap("agent", SlotState.OFFLINE, None)])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.errors == [("agent", "spawn failed")]
        assert report.loaded == []

    async def test_converge_requires_slot_manager(self) -> None:
        with pytest.raises(RuntimeError):
            await StackApplyEngine().converge(_stack(StackSlotEntry(slot="agent", model="m")))
