"""Tests for the model-tune-ownership one-shot migrator (spec-hw-slot-ownership §1).

Covers: sole-slot fold, consensus multi-slot fold, divergent-share REFUSAL
(slot-vs-slot AND slot-vs-existing-model-default — the exact "two writers
disagree" bug this migration exists to fix), idempotence, the slot key-drop
side effect (and its exclusion for a refused model), dry-run (writes
nothing), and the deploy-window write gate.
"""

from __future__ import annotations

import tomllib

import pytest

from hal0.config.migrations.model_tune_ownership import (
    DeployWindowRequired,
    apply_fold_plan,
    plan_tune_fold,
)


class _FakeRegistry:
    """Minimal ModelRegistry stand-in recording .update() calls."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def update(self, model_id: str, updates: dict) -> None:
        self.updates.append((model_id, updates))


def _slot(name: str, model: str, **tune) -> dict:
    cfg: dict = {"name": name, "model": {"default": model}}
    cfg.update(tune)
    return cfg


# ── planner: sole-slot fold ──────────────────────────────────────────────────


def test_sole_slot_mtp_folds_onto_model():
    plan = plan_tune_fold([_slot("s", "m", mtp=True)], {})
    assert plan.ok
    assert len(plan.folds) == 1
    fold = plan.folds[0]
    assert fold.model_id == "m"
    assert fold.updates == {"mtp": True}
    assert fold.new_defaults == {"mtp": True}
    assert plan.slot_key_drops == {"s": ("mtp",)}


def test_sole_slot_all_three_fields_fold_together():
    plan = plan_tune_fold([_slot("s", "m", mtp=False, enable_thinking=True, vision=False)], {})
    assert plan.ok
    fold = plan.folds[0]
    assert fold.updates == {"mtp": False, "enable_thinking": True, "vision": False}
    assert set(plan.slot_key_drops["s"]) == {"mtp", "enable_thinking", "vision"}


def test_slot_with_no_tune_keys_contributes_nothing():
    plan = plan_tune_fold([{"name": "s", "model": {"default": "m"}}], {})
    assert plan.folds == []
    assert plan.slot_key_drops == {}


def test_slot_with_no_bound_model_is_ignored():
    plan = plan_tune_fold([{"name": "s", "mtp": True}], {})
    # No [model].default -> nothing to fold onto, but the key-drop bookkeeping
    # still sees the key present (harmless — no model to conflict with).
    assert plan.folds == []
    assert plan.slot_key_drops == {"s": ("mtp",)}


# ── planner: consensus multi-slot fold ───────────────────────────────────────


def test_two_slots_agreeing_fold_once():
    plan = plan_tune_fold([_slot("a", "m", mtp=True), _slot("b", "m", mtp=True)], {})
    assert plan.ok
    assert len(plan.folds) == 1
    assert plan.folds[0].slot_names == ("a", "b")
    assert set(plan.slot_key_drops) == {"a", "b"}


# ── planner: divergent-share REFUSAL ─────────────────────────────────────────


def test_two_slots_disagreeing_refuse_the_model():
    plan = plan_tune_fold([_slot("a", "m", mtp=True), _slot("b", "m", mtp=False)], {})
    assert not plan.ok
    assert plan.folds == []
    assert len(plan.refusals) == 1
    r = plan.refusals[0]
    assert r.model_id == "m"
    assert r.field == "mtp"
    assert r.votes == {"a": True, "b": False}
    # Neither slot's keys are dropped — the operator needs the raw TOML to resolve.
    assert plan.slot_key_drops == {}


def test_slot_disagreeing_with_existing_model_default_refuses():
    """The exact bug this migration exists to fix: a slot pill and the model
    drawer persisting DIFFERENT values for the same fact."""
    plan = plan_tune_fold([_slot("a", "m", vision=False)], {"m": {"vision": True}})
    assert not plan.ok
    r = plan.refusals[0]
    assert r.field == "vision"
    assert r.votes == {"<model default>": True, "a": False}


def test_slot_agreeing_with_existing_model_default_is_a_noop():
    plan = plan_tune_fold([_slot("a", "m", mtp=True)], {"m": {"mtp": True}})
    assert plan.ok
    assert plan.folds == []
    assert any(mid == "m" for mid, _ in plan.skipped)
    # Still drops the now-redundant slot key even though the model needed no write.
    assert plan.slot_key_drops == {"a": ("mtp",)}


def test_only_one_field_diverging_does_not_block_other_models():
    plan = plan_tune_fold(
        [
            _slot("a", "m1", mtp=True),
            _slot("b", "m1", mtp=False),
            _slot("c", "m2", vision=True),
        ],
        {},
    )
    assert not plan.ok
    assert {r.model_id for r in plan.refusals} == {"m1"}
    assert [f.model_id for f in plan.folds] == ["m2"]
    assert plan.slot_key_drops == {"c": ("vision",)}


# ── idempotence ───────────────────────────────────────────────────────────────


def test_idempotent_rerun_is_noop():
    plan1 = plan_tune_fold([_slot("s", "m", mtp=True, enable_thinking=False)], {})
    # Simulate the fold having already landed on the model, key already dropped.
    plan2 = plan_tune_fold(
        [{"name": "s", "model": {"default": "m"}}],
        {"m": plan1.folds[0].new_defaults},
    )
    assert plan2.folds == []
    assert plan2.slot_key_drops == {}
    assert any(mid == "m" for mid, _ in plan2.skipped) is False  # nothing referenced it


# ── applier: dry-run / deploy-window gate ────────────────────────────────────


def test_dry_run_writes_nothing_and_reports_the_plan():
    plan = plan_tune_fold([_slot("s", "m", mtp=True)], {})
    reg = _FakeRegistry()
    lines = apply_fold_plan(plan, reg, dry_run=True)
    assert any("would fold" in ln for ln in lines)
    assert any("would drop" in ln for ln in lines)
    assert reg.updates == []


def test_write_requires_deploy_window_ack():
    plan = plan_tune_fold([_slot("s", "m", mtp=True)], {})
    reg = _FakeRegistry()
    with pytest.raises(DeployWindowRequired):
        apply_fold_plan(plan, reg, deploy_window=False, dry_run=False)


def test_refusal_raises_and_writes_nothing():
    plan = plan_tune_fold([_slot("a", "m", mtp=True), _slot("b", "m", mtp=False)], {})
    reg = _FakeRegistry()
    with pytest.raises(RuntimeError, match="divergent"):
        apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert reg.updates == []


# ── applier: real registry.update() + slot-TOML key-drop surgery ────────────


def test_apply_updates_registry_and_drops_slot_keys(tmp_path, monkeypatch):
    from hal0.config import paths

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "chat.toml").write_text(
        'name = "chat"\nmtp = true\nenable_thinking = false\n[model]\ndefault = "m"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)

    slots = [
        {
            "name": "chat",
            "mtp": True,
            "enable_thinking": False,
            "model": {"default": "m"},
        }
    ]
    plan = plan_tune_fold(slots, {})
    reg = _FakeRegistry()
    apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)

    assert reg.updates == [("m", {"defaults": {"mtp": True, "enable_thinking": False}})]
    slot_after = tomllib.loads((slots_dir / "chat.toml").read_text(encoding="utf-8"))
    assert "mtp" not in slot_after
    assert "enable_thinking" not in slot_after
    assert slot_after["model"]["default"] == "m"  # unrelated keys untouched


def test_apply_leaves_refused_slot_toml_untouched(tmp_path, monkeypatch):
    from hal0.config import paths

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "a.toml").write_text(
        'name = "a"\nmtp = true\n[model]\ndefault = "m"\n', encoding="utf-8"
    )
    (slots_dir / "b.toml").write_text(
        'name = "b"\nmtp = false\n[model]\ndefault = "m"\n', encoding="utf-8"
    )
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)

    slots = [
        {"name": "a", "mtp": True, "model": {"default": "m"}},
        {"name": "b", "mtp": False, "model": {"default": "m"}},
    ]
    plan = plan_tune_fold(slots, {})
    reg = _FakeRegistry()
    with pytest.raises(RuntimeError):
        apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)

    # Refusal aborts the WHOLE apply before any write — both slot TOMLs
    # (and the registry) are untouched.
    assert reg.updates == []
    assert "mtp = true" in (slots_dir / "a.toml").read_text(encoding="utf-8")
    assert "mtp = false" in (slots_dir / "b.toml").read_text(encoding="utf-8")
