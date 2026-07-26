"""Tests for the model-owned-caps one-shot migrator (spec-hw-slot-ownership §1).

Covers:

  * fold: a slot's explicit mtp/enable_thinking/vision value folds into its
    bound model's defaults, only when the model has no opinion yet.
  * an existing model default is never clobbered.
  * divergent slots bound to the same model report the conflict (first slot,
    stable order, wins) instead of silently dropping one value.
  * the slot's key is always dropped from its TOML once processed.
  * idempotent re-run is a no-op.
  * applier: dry-run writes nothing; the deploy-window gate; real file
    surgery + model-registry write.
"""

from __future__ import annotations

import tomllib

import pytest

from hal0.config.migrations.model_owned_caps import (
    CapFoldPlan,
    DeployWindowRequired,
    ModelCapFold,
    SlotCapDrop,
    apply_fold_plan,
    plan_fold,
)


def _slot(name: str, model: str | None = None, **caps) -> dict:
    """Raw slot-TOML dict. ``mtp``/``enable_thinking``/``vision`` kwargs (bool)
    seed the top-level keys the migration is folding away."""
    cfg: dict = {"name": name}
    if model is not None:
        cfg["model"] = {"default": model}
    cfg.update(caps)
    return cfg


# ── planner: basic fold ──────────────────────────────────────────────────────


def test_slot_mtp_folds_to_model_when_model_has_no_opinion():
    plan = plan_fold([_slot("s", "m", mtp=True)], {})
    fold = next(f for f in plan.model_folds if f.model_id == "m")
    assert fold.updates == {"mtp": True}
    assert plan.slot_drops == [SlotCapDrop("s", ("mtp",))]


def test_all_three_keys_fold_together():
    plan = plan_fold([_slot("s", "m", mtp=True, enable_thinking=False, vision=False)], {})
    fold = next(f for f in plan.model_folds if f.model_id == "m")
    assert fold.updates == {"mtp": True, "enable_thinking": False, "vision": False}
    assert set(plan.slot_drops[0].drop_keys) == {"mtp", "enable_thinking", "vision"}


def test_existing_model_default_is_never_clobbered():
    """A curator-set model default always wins over slot debris — the slot
    key is still dropped (it's inert either way), but the model value is
    left exactly as the curator set it."""
    plan = plan_fold([_slot("s", "m", mtp=False)], {"m": {"mtp": True}})
    assert plan.model_folds == []  # nothing to fold — model already has an opinion
    assert plan.slot_drops == [SlotCapDrop("s", ("mtp",))]  # slot debris still dropped


def test_no_model_binding_no_fold_but_still_no_drop_target_leak():
    """A slot with no [model].default has nothing to fold onto — its own
    cap key is still reported as drop debris (it's meaningless without a
    bound model), but no model_folds entry is produced."""
    plan = plan_fold([_slot("s", None, mtp=True)], {})
    assert plan.model_folds == []
    assert plan.slot_drops == [SlotCapDrop("s", ("mtp",))]


def test_absent_slot_key_produces_no_drop():
    plan = plan_fold([_slot("s", "m")], {})
    assert plan.slot_drops == []
    assert plan.model_folds == []


# ── divergence: two slots bound to the same model disagree ─────────────────


def test_divergent_slots_first_wins_and_is_reported():
    plan = plan_fold([_slot("s1", "m", mtp=True), _slot("s2", "m", mtp=False)], {})
    fold = next(f for f in plan.model_folds if f.model_id == "m")
    assert fold.updates == {"mtp": True}  # s1 (first, stable order) won
    assert len(plan.divergent) == 1
    d = plan.divergent[0]
    assert d.model_id == "m" and d.key == "mtp"
    assert d.chosen_slot == "s1" and d.chosen_value is True
    assert d.conflicting_slot == "s2" and d.conflicting_value is False
    # Both slots still get their own debris dropped.
    assert {sd.slot_name for sd in plan.slot_drops} == {"s1", "s2"}


def test_agreeing_slots_do_not_report_divergence():
    plan = plan_fold([_slot("s1", "m", mtp=True), _slot("s2", "m", mtp=True)], {})
    assert plan.divergent == []
    fold = next(f for f in plan.model_folds if f.model_id == "m")
    assert fold.updates == {"mtp": True}


# ── idempotent re-run ─────────────────────────────────────────────────────────


def test_idempotent_rerun_is_noop():
    # First pass: fold mtp onto the model.
    plan = plan_fold([_slot("s", "m", mtp=True)], {})
    assert plan.model_folds and plan.model_folds[0].updates == {"mtp": True}

    # Second pass models the post-migration state: the slot key is gone, the
    # model now carries the folded value.
    plan2 = plan_fold([_slot("s", "m")], {"m": {"mtp": True}})
    assert plan2.model_folds == []
    assert plan2.slot_drops == []


# ── applier: dry-run + deploy-window gate + real file surgery ──────────────────


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    from hal0.config import paths

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "s.toml").write_text(
        'name = "s"\nmtp = true\n[model]\ndefault = "m"\n', encoding="utf-8"
    )
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)

    plan = CapFoldPlan(
        slot_drops=[SlotCapDrop("s", ("mtp",))],
        model_folds=[ModelCapFold("m", {"mtp": True})],
    )
    lines = apply_fold_plan(plan, dry_run=True)
    assert any("would fold" in ln for ln in lines)
    assert any("would drop" in ln for ln in lines)
    # untouched on disk
    assert "mtp = true" in (slots_dir / "s.toml").read_text(encoding="utf-8")


def test_write_requires_deploy_window_ack():
    plan = CapFoldPlan(model_folds=[ModelCapFold("m", {"mtp": True})])
    with pytest.raises(DeployWindowRequired):
        apply_fold_plan(plan, deploy_window=False, dry_run=False)


def test_apply_surgery_drops_slot_keys_and_writes_model(tmp_path, monkeypatch):
    from hal0.config import paths
    from hal0.config.migrations import model_owned_caps as mod

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "s.toml").write_text(
        'name = "s"\nmtp = true\nenable_thinking = false\n[model]\ndefault = "m"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)

    # Stub the registry write so this test stays DB-free; record what was folded.
    folded: list = []
    monkeypatch.setattr(mod, "_apply_model_folds", lambda folds, **kw: folded.extend(folds))

    plan = plan_fold([_slot("s", "m", mtp=True, enable_thinking=False)], {})
    lines = apply_fold_plan(plan, deploy_window=True, dry_run=False)

    slot_after = tomllib.loads((slots_dir / "s.toml").read_text(encoding="utf-8"))
    assert "mtp" not in slot_after
    assert "enable_thinking" not in slot_after
    assert slot_after["model"]["default"] == "m"  # untouched sibling key

    assert len(folded) == 1
    assert folded[0].model_id == "m"
    assert folded[0].updates == {"mtp": True, "enable_thinking": False}
    assert any("drop slot" in ln for ln in lines)
    assert any("fold model" in ln for ln in lines)
