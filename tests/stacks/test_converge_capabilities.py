"""Tests for converge() capability-child routing pass.

Targeted file run:
    cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_capabilities.py -q
"""

from __future__ import annotations

from hal0.config.schema import StackCapabilityRow, StackConfig, StackSlotEntry
from hal0.stacks.apply import StackApplyEngine
from tests.stacks.conftest import RecordingOrchestrator, RecordingSlotManager


def _engine(orch: RecordingOrchestrator) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=RecordingSlotManager([]), orchestrator=orch)


def _row(child: str, **kw: object) -> StackCapabilityRow:
    base = {"child": child, "device": "npu", "provider": "flm", "model": "bge-m3", "enabled": True}
    base.update(kw)
    return StackCapabilityRow(**base)


class TestCapabilityRouting:
    async def test_embed_row_routes_to_embed_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(
            name="S", slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed")])]
        )
        report = await _engine(orch).converge(stack)
        assert orch.calls == [
            (
                "embed",
                "embed",
                {"device": "npu", "provider": "flm", "model": "bge-m3", "enabled": True},
            )
        ]
        assert report.capabilities_applied == ["embed/embed"]

    async def test_rerank_routes_to_embed_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(
            name="S", slots=[StackSlotEntry(slot="rerank", capabilities=[_row("rerank")])]
        )
        await _engine(orch).converge(stack)
        assert orch.calls[0][0] == "embed" and orch.calls[0][1] == "rerank"

    async def test_stt_and_tts_route_to_voice_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(
            name="S",
            slots=[StackSlotEntry(slot="voice", capabilities=[_row("stt"), _row("tts")])],
        )
        await _engine(orch).converge(stack)
        groups = {(c[0], c[1]) for c in orch.calls}
        assert ("voice", "stt") in groups and ("voice", "tts") in groups

    async def test_disabled_row_is_not_applied(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(
            name="S",
            slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed", enabled=False)])],
        )
        report = await _engine(orch).converge(stack)
        assert orch.calls == []
        assert report.capabilities_applied == []

    async def test_unknown_child_is_recorded_as_error(self) -> None:
        orch = RecordingOrchestrator()
        # `child` has no schema validator (any string is accepted), so an
        # unmapped child constructs fine and must be reported, not applied.
        bad = StackCapabilityRow(child="nope", device="npu", provider="flm", model="m")
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="x", capabilities=[bad])])
        report = await _engine(orch).converge(stack)
        assert orch.calls == []
        assert report.errors and report.errors[0][0] == "capability:nope"

    async def test_apply_failure_is_recorded(self) -> None:
        class Boom(RecordingOrchestrator):
            async def apply(self, slot, child, partial):
                raise RuntimeError("orch down")

        orch = Boom()
        stack = StackConfig(
            name="S", slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed")])]
        )
        report = await _engine(orch).converge(stack)
        assert report.errors == [("embed/embed", "orch down")]
        assert report.capabilities_applied == []


def test_child_maps_mirror_orchestrator_source_of_truth() -> None:
    """Canary: the hardcoded child maps must exactly reverse the orchestrator's
    _CHILD_TO_SLOT. Imported INSIDE the test so this module stays free of the
    capabilities import cycle the maps are hardcoded to avoid.
    """
    from hal0.capabilities.orchestrator import _CHILD_TO_SLOT
    from hal0.stacks.apply import _CHILD_TO_GROUP, _CHILD_TO_SLOT_NAME

    expected_group = {child: group for (group, child) in _CHILD_TO_SLOT}
    expected_slot = {child: slot for (group, child), slot in _CHILD_TO_SLOT.items()}
    assert expected_group == _CHILD_TO_GROUP
    assert expected_slot == _CHILD_TO_SLOT_NAME


def test_retired_vision_child_is_skipped_not_errored() -> None:
    """A stack saved before the vision-lane retirement carries a
    StackCapabilityRow(child="vision"); applying it must skip the row
    quietly instead of failing convergence."""
    from hal0.stacks.apply import _CHILD_TO_GROUP, _CHILD_TO_SLOT_NAME, _RETIRED_CHILDREN

    assert "vision" in _RETIRED_CHILDREN
    assert "vision" not in _CHILD_TO_GROUP
    assert "vision" not in _CHILD_TO_SLOT_NAME
