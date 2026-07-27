"""Unit tests for StackApplyEngine.plan() — compute-only Stack→ChangeSet.

Targeted file run:
    cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_plan.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.slot_config import ChangeSet
from hal0.stacks.apply import StackApplyEngine, StackChangePlan


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_agent_slot(home: str) -> Path:
    path = _slots_dir(home) / "agent.toml"
    path.write_text(
        "\n".join(
            [
                'name = "agent"',
                "port = 8087",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "vision = false",
                "[model]",
                'default = "old-model"',
                "context_size = 8192",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _stack() -> StackConfig:
    return StackConfig(
        name="Saber",
        slots=[
            StackSlotEntry(
                slot="agent",
                model="chadrock-35b-ace-saber",
                device="gpu-rocm",
                vision=True,
            )
        ],
    )


class TestPlanComputeOnly:
    def test_plan_writes_nothing(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        before_bytes = slot_path.read_bytes()
        engine = StackApplyEngine()
        plan = engine.plan("saber", _stack())
        assert isinstance(plan, StackChangePlan)
        assert isinstance(plan.change_set, ChangeSet)
        assert slot_path.read_bytes() == before_bytes, "plan() must not touch disk"

    def test_before_matches_disk(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        by_path = {fs.path: fs.data for fs in plan.change_set.before}
        assert by_path[slot_path] == _read(slot_path)


class TestReconciliation:
    def test_after_sets_model_device_vision(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after["model"]["default"] == "chadrock-35b-ace-saber"
        assert after["model"]["context_size"] == 8192, (
            "sibling [model] keys must survive deep-merge"
        )
        # P2-device: device is the sole persisted truth — the legacy
        # ``backend`` mirror is no longer written.
        assert after["device"] == "gpu-rocm"
        assert "backend" not in after
        # spec-hw-slot-ownership §1: ``vision`` is MODEL-owned. The stack entry
        # still carries it (schema back-compat / portable export) but the
        # reconcile MUST NOT project it onto the slot — same partition
        # ``_create_missing_slots`` applies to stack-CREATED slots (#1356).
        # The fixture seeds ``vision = false`` on disk and the stack row says
        # ``True``; the on-disk value surviving is what proves the stack no
        # longer writes this key. (It is not deleted either — that is
        # ``hal0 slot migrate-caps``' job, not a stack write's.)
        assert after["vision"] is False, "stack must not overwrite a model-owned slot key"

    def test_changed_true_when_model_differs(self, tmp_hal0_home: str) -> None:
        _write_agent_slot(tmp_hal0_home)
        assert StackApplyEngine().plan("saber", _stack()).change_set.changed is True

    def test_missing_slot_file_is_skipped(self, tmp_hal0_home: str) -> None:
        # No agent.toml on disk → slot creation is out of 2a scope → after == before (None).
        _slots_dir(tmp_hal0_home)  # dir exists, file does not
        plan = StackApplyEngine().plan("saber", _stack())
        assert plan.change_set.changed is False
        assert all(fs.data is None for fs in plan.change_set.before)

    def test_summary_lists_changed_slot(self, tmp_hal0_home: str) -> None:
        _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        assert any("agent" in line for line in plan.summary)

    def test_stack_apply_leaves_legacy_on_disk_vision_untouched(self, tmp_hal0_home: str) -> None:
        """A pre-migration slot's ``vision`` survives a stack apply unchanged.

        Was ``test_vision_false_overwrites_on_disk_true``, which asserted the
        opposite: that a stack row's ``vision`` overwrote the slot's. That
        encoded the defect this lane fixes — stack apply was the last writer
        still projecting a MODEL-owned key onto a slot after #1356 closed the
        create path.

        The stack is not the cleanup mechanism for legacy slot caps. A slot
        whose TOML still carries a folded-away ``vision``/``mtp`` is cleaned by
        the one-shot ``hal0 slot migrate-caps`` fold and by
        ``updater._strip_ineligible_slot_mtp`` — so the value is left exactly as
        found here, neither overwritten nor deleted.
        """
        slot_path = _slots_dir(tmp_hal0_home) / "agent.toml"
        slot_path.write_text(
            "\n".join(
                ['name = "agent"', "port = 8087", "vision = true", "[model]", 'default = "old"', ""]
            ),
            encoding="utf-8",
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="m", vision=False)])
        plan = StackApplyEngine().plan("s", stack)
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after["vision"] is True, "stack apply must not rewrite a model-owned slot key"
        assert after["model"]["default"] == "m", "the model binding still applies"


class TestGuardedReconcile:
    """The stack write path shares SlotManager's guard pipeline.

    Pre-fix ``_reconciled_stack_slot`` hand-rolled its merge, skipping the
    ctx_size fold, device↔profile coherence, and the NPU/default guards —
    a stack could persist a vulkan-device+rocm-profile pair that
    ``update_config`` would refuse.
    """

    def _write_slot(self, home: str, name: str, lines: list[str]) -> Path:
        path = _slots_dir(home) / f"{name}.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_conflicting_device_profile_is_flagged_not_applied(self, tmp_hal0_home: str) -> None:
        _write_agent_slot(tmp_hal0_home)
        stack = StackConfig(
            name="Bad",
            slots=[
                StackSlotEntry(
                    slot="agent",
                    model="m",
                    device="gpu-vulkan",
                    profile="chat",  # device-agnostic workload profile, no conflict
                )
            ],
        )
        plan = StackApplyEngine().plan("bad", stack)
        # 1.0: profiles are device-agnostic workload names — no backend
        # conflict to flag. The plan succeeds without errors.
        assert plan.errors == []
        assert any("agent" in line for line in plan.summary)

    def test_device_flip_repoints_stale_profile(self, tmp_hal0_home: str) -> None:
        """A stack that moves device across backends keeps the workload profile.

        1.0: profiles are device-agnostic workload names — device flip
        does NOT change the profile."""
        slot_path = self._write_slot(
            tmp_hal0_home,
            "agent",
            [
                'name = "agent"',
                "port = 8087",
                'device = "gpu-vulkan"',
                'profile = "chat"',
                "[model]",
                'default = "old"',
            ],
        )
        stack = StackConfig(
            name="S",
            slots=[StackSlotEntry(slot="agent", model="m", device="gpu-rocm")],
        )
        plan = StackApplyEngine().plan("s", stack)
        assert plan.errors == []
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after["device"] == "gpu-rocm"
        assert "backend" not in after
        # 1.0: profile stays the workload name; device flip doesn't repoint it.
        assert after["profile"] == "chat"

    def test_ctx_size_alias_folded_by_stack_write(self, tmp_hal0_home: str) -> None:
        slot_path = self._write_slot(
            tmp_hal0_home,
            "agent",
            [
                'name = "agent"',
                "port = 8087",
                "[model]",
                'default = "old"',
                "ctx_size = 4096",
            ],
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="m")])
        plan = StackApplyEngine().plan("s", stack)
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after["model"]["context_size"] == 4096
        assert "ctx_size" not in after["model"]

    def test_second_npu_anchor_is_flagged(self, tmp_hal0_home: str) -> None:
        self._write_slot(
            tmp_hal0_home,
            "npu",
            [
                'name = "npu"',
                "port = 8090",
                'device = "npu"',
                'type = "llm"',
                "enabled = true",
                "[model]",
                'default = "qwen3.5:4b"',
            ],
        )
        slot_path = self._write_slot(
            tmp_hal0_home,
            "npu2",
            [
                'name = "npu2"',
                "port = 8091",
                'device = "npu"',
                'type = "llm"',
                "enabled = true",
                "[model]",
                'default = "old"',
            ],
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="npu2", model="gemma4-it:e2b")])
        plan = StackApplyEngine().plan("s", stack)
        assert plan.errors and plan.errors[0][0] == "npu2"
        assert "NPU" in plan.errors[0][1]
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after == _read(slot_path)
