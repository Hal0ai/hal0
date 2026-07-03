"""Unit tests for the active-stack pointer + drift detection.

Targeted file run:
    cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_drift.py -q
"""

from __future__ import annotations

from pathlib import Path

from hal0.config import paths
from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.slots.state import SlotState
from hal0.stacks import StacksCatalog
from hal0.stacks.apply import StackApplyEngine
from hal0.stacks.state import (
    StackStateRecord,
    read_stack_state,
    stack_content_hash,
    write_stack_state_atomic,
)
from tests.stacks.conftest import FakeSnap, RecordingOrchestrator, RecordingSlotManager


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_slot(home: str, name: str, model: str) -> Path:
    path = _slots_dir(home) / f"{name}.toml"
    path.write_text(
        "\n".join([f'name = "{name}"', "port = 8087", "[model]", f'default = "{model}"', ""]),
        encoding="utf-8",
    )
    return path


def _saber() -> StackConfig:
    return StackConfig(name="Saber", slots=[StackSlotEntry(slot="agent", model="ace-saber")])


class TestStateRecord:
    def test_round_trip(self, tmp_hal0_home: str) -> None:
        p = paths.stacks_state_path()
        rec = StackStateRecord(active_slug="saber", content_hash="abc123", applied_at=1.5)
        write_stack_state_atomic(p, rec)
        got = read_stack_state(p)
        assert got is not None
        assert got.active_slug == "saber"
        assert got.content_hash == "abc123"

    def test_read_missing_returns_none(self, tmp_hal0_home: str) -> None:
        assert read_stack_state(paths.stacks_state_path()) is None

    def test_corrupt_state_returns_none(self, tmp_hal0_home: str) -> None:
        p = paths.stacks_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not json <<<", encoding="utf-8")
        assert read_stack_state(p) is None


class TestContentHash:
    def test_stable_and_order_independent(self) -> None:
        a = stack_content_hash(
            {"agent": {"model": {"default": "x"}}, "chat": {"model": {"default": "y"}}}
        )
        b = stack_content_hash(
            {"chat": {"model": {"default": "y"}}, "agent": {"model": {"default": "x"}}}
        )
        assert a == b, "hash must be key-order independent"

    def test_changes_with_content(self) -> None:
        assert stack_content_hash({"agent": {"model": {"default": "x"}}}) != stack_content_hash(
            {"agent": {"model": {"default": "z"}}}
        )


class TestDriftStatus:
    def test_no_pointer_is_none(self, tmp_hal0_home: str) -> None:
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        assert StackApplyEngine().drift_status(catalog) == {"active": None, "status": "none"}

    def test_clean_right_after_apply(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())
        engine = StackApplyEngine()
        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)
        assert engine.drift_status(catalog) == {"active": "saber", "status": "clean"}

    def test_modified_after_hand_edit(self, tmp_hal0_home: str) -> None:
        slot_path = _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())
        engine = StackApplyEngine()
        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)
        # Hand-edit the slot after applying → drift.
        slot_path.write_text(slot_path.read_text() + '\nrole = "primary"\n', encoding="utf-8")
        assert engine.drift_status(catalog)["status"] == "modified"


class TestConvergeDegraded:
    """PS-5 part 2 — a converge whose per-slot loads failed must not read clean.

    The slot TOMLs are committed cleanly (Phase A), so the drift hash still
    matches live disk; without the converge_ok flag drift_status would wrongly
    report ``clean`` even though the runtime never came up. It must report
    ``degraded`` instead.
    """

    async def test_load_error_reports_degraded_not_clean(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())

        class Boom(RecordingSlotManager):
            async def load(self, slot_name, model_id=None):
                raise RuntimeError("spawn failed")

        sm = Boom([FakeSnap("agent", SlotState.OFFLINE, None)])
        engine = StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())

        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        report = await engine.converge(_saber())
        assert report.errors, "converge should have recorded a per-slot load error"
        engine.record_active(plan, applied_at=1.0, converge_ok=not report.errors)

        status = engine.drift_status(catalog)
        assert status == {"active": "saber", "status": "degraded"}

    async def test_clean_converge_still_reports_clean(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())

        sm = RecordingSlotManager([FakeSnap("agent", SlotState.OFFLINE, None)])
        engine = StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())

        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        report = await engine.converge(_saber())
        assert not report.errors
        engine.record_active(plan, applied_at=1.0, converge_ok=not report.errors)

        assert engine.drift_status(catalog) == {"active": "saber", "status": "clean"}
