"""Tests for the HW-slot-ownership one-shot migrator (spec-hw-slot-ownership §6).

Covers every fold + edge the spec §9 verification line calls out:

  * NGL fold (model column → slot; slot's own nested [model].n_gpu_layers wins;
    already-set slot NGL is left alone; the -1 sentinel is "unset").
  * BINARY fold (model.preferred_runner → slot.binary; already-set left alone).
  * profile.image DELIBERATE pin → image_pin of every referencing slot.
  * profile.image former-default DEBRIS (STALE_RUNNER_IMAGE_REFS) → dropped,
    never folded.
  * zero-slot profile → "lost" pin logged, still deleted.
  * slot.image / [slot].image → image_pin (slot's own image wins over profile's).
  * model_nulls: the folded-out columns are marked for cleanup.
  * idempotent re-run is a no-op.
  * applier: dry-run writes nothing; the deploy-window gate; real file surgery.

These drive the PLANNER (pure) + APPLIER (raw-TOML surgery) directly; the
applier operates on raw TOML dicts, so it is independent of whether the
ProfileConfig schema field is still required (field-retirement is a separate
lane — spec §6.3 / the profile.image-delete follow-up).
"""

from __future__ import annotations

import tomllib

import pytest

from hal0.config.migrations.hw_slot_ownership import (
    DeployWindowRequired,
    ModelHw,
    SlotFold,
    UnfoldPlan,
    apply_unfold_plan,
    plan_unfold,
)

_STALE = frozenset({"ghcr.io/hal0ai/old:server", "localhost/hal0-rocmfpx:former-default"})


def _slot(name: str, model: str | None = None, **kw) -> dict:
    """Raw slot-TOML dict. ``nested_ngl``/``binary``/``image_pin``/``image``/
    ``top_ngl``/``profile`` are optional keys."""
    cfg: dict = {"name": name}
    model_tbl: dict = {}
    if model is not None:
        model_tbl["default"] = model
    if "nested_ngl" in kw:
        model_tbl["n_gpu_layers"] = kw["nested_ngl"]
    if model_tbl:
        cfg["model"] = model_tbl
    if "profile" in kw:
        cfg["profile"] = kw["profile"]
    if "top_ngl" in kw:
        cfg["n_gpu_layers"] = kw["top_ngl"]
    if "binary" in kw:
        cfg["binary"] = kw["binary"]
    if "image_pin" in kw:
        cfg["image_pin"] = kw["image_pin"]
    if "image" in kw:
        cfg["image"] = kw["image"]
    return cfg


def _plan(slots, model_hw=None, profile_images=None) -> UnfoldPlan:
    return plan_unfold(
        slots,
        model_hw or {},
        profile_images or {},
        stale_refs=_STALE,
    )


# ── Fold 1: NGL ───────────────────────────────────────────────────────────────


def test_model_ngl_folds_to_slot_when_unset():
    plan = _plan([_slot("s", "m")], {"m": ModelHw(32, None)})
    fold = plan.slot_fold("s")
    assert fold is not None and fold.set_ngl == 32


def test_slot_nested_ngl_wins_over_model_default():
    plan = _plan([_slot("s", "m", nested_ngl=10)], {"m": ModelHw(99, None)})
    fold = plan.slot_fold("s")
    assert fold.set_ngl == 10  # slot's own nested override beats the model column
    assert fold.drop_nested_ngl is True  # sunset the nested key


def test_already_set_slot_ngl_is_not_clobbered():
    plan = _plan([_slot("s", "m", top_ngl=7)], {"m": ModelHw(32, None)})
    fold = plan.slot_fold("s")
    assert fold is None or fold.set_ngl is None  # slot already authoritative


def test_ngl_minus_one_sentinel_is_unset():
    # A -1 ("all layers") nested/top value is the sentinel → no fold, and a
    # model column of -1 contributes nothing either.
    plan = _plan([_slot("s", "m", nested_ngl=-1, top_ngl=-1)], {"m": ModelHw(-1, None)})
    fold = plan.slot_fold("s")
    assert fold is None or fold.set_ngl is None


# ── Fold 2: BINARY ────────────────────────────────────────────────────────────


def test_preferred_runner_folds_to_binary_when_empty():
    plan = _plan([_slot("s", "m", binary="")], {"m": ModelHw(None, "rocmfpx")})
    fold = plan.slot_fold("s")
    assert fold is not None and fold.set_binary == "rocmfpx"


def test_already_set_binary_is_not_clobbered():
    plan = _plan([_slot("s", "m", binary="vulkanfpx")], {"m": ModelHw(None, "rocmfpx")})
    fold = plan.slot_fold("s")
    assert fold is None or fold.set_binary is None


# ── Fold 3: profile.image ─────────────────────────────────────────────────────


def test_deliberate_profile_pin_folds_to_referencing_slots():
    slots = [_slot("a", "m", profile="p"), _slot("b", "m", profile="p")]
    plan = _plan(slots, {}, {"p": "ghcr.io/hal0ai/custom:debug"})
    assert plan.slot_fold("a").set_image_pin == "ghcr.io/hal0ai/custom:debug"
    assert plan.slot_fold("b").set_image_pin == "ghcr.io/hal0ai/custom:debug"
    act = next(a for a in plan.profile_actions if a.profile_name == "p")
    assert act.disposition == "folded"


def test_stale_profile_pin_is_debris_not_folded():
    stale = next(iter(_STALE))
    slots = [_slot("a", "m", profile="p")]
    plan = _plan(slots, {}, {"p": stale})
    # debris → dropped from the profile, NEVER copied onto the slot
    fold = plan.slot_fold("a")
    assert fold is None or fold.set_image_pin is None
    act = next(a for a in plan.profile_actions if a.profile_name == "p")
    assert act.disposition == "debris"


def test_zero_slot_profile_pin_is_lost():
    plan = _plan([], {}, {"orphan": "ghcr.io/hal0ai/custom:x"})
    act = next(a for a in plan.profile_actions if a.profile_name == "orphan")
    assert act.disposition == "lost"


# ── Fold 4: slot.image ────────────────────────────────────────────────────────


def test_slot_own_image_folds_to_image_pin_and_collapses():
    plan = _plan([_slot("s", "m", image="ghcr.io/hal0ai/mine:v2")])
    fold = plan.slot_fold("s")
    assert fold.set_image_pin == "ghcr.io/hal0ai/mine:v2"
    assert fold.drop_slot_image is True


def test_slot_own_image_wins_over_profile_pin():
    plan = _plan(
        [_slot("s", "m", profile="p", image="ghcr.io/hal0ai/slotwins:v1")],
        {},
        {"p": "ghcr.io/hal0ai/profile:v1"},
    )
    assert plan.slot_fold("s").set_image_pin == "ghcr.io/hal0ai/slotwins:v1"


def test_stale_slot_image_is_dropped_not_pinned():
    stale = next(iter(_STALE))
    fold = _plan([_slot("s", "m", image=stale)]).slot_fold("s")
    assert fold.set_image_pin is None  # debris never becomes a pin
    assert fold.drop_slot_image is True  # but the stale key is still collapsed


def test_nested_slot_table_image_is_read_and_collapsed():
    cfg = {"slot": {"name": "s", "image": "ghcr.io/hal0ai/nested:v1"}, "model": {"default": "m"}}
    fold = _plan([cfg]).slot_fold("s")
    assert fold.set_image_pin == "ghcr.io/hal0ai/nested:v1"
    assert fold.drop_slot_image is True


def test_image_gen_table_is_not_treated_as_pin():
    # The [image] image-gen table (a dict) must never be mistaken for a pin.
    cfg = {"name": "s", "model": {"default": "m"}, "image": {"default_size": "1024x1024"}}
    fold = _plan([cfg]).slot_fold("s")
    assert fold is None or (fold.set_image_pin is None and not fold.drop_slot_image)


# ── model_nulls + idempotence ─────────────────────────────────────────────────


def test_model_columns_marked_for_null():
    plan = _plan(
        [_slot("s", "m")],
        {"m": ModelHw(32, "rocmfpx"), "clean": ModelHw(None, None)},
    )
    assert "m" in plan.model_nulls
    assert "clean" not in plan.model_nulls  # nothing to clear


def test_idempotent_rerun_is_noop():
    # First pass folds ngl+binary+image onto the slot.
    slots = [_slot("s", "m", image="ghcr.io/hal0ai/mine:v2")]
    plan = _plan(slots, {"m": ModelHw(32, "rocmfpx")})
    fold = plan.slot_fold("s")
    assert fold.set_ngl == 32 and fold.set_binary == "rocmfpx"

    # Second pass models the post-migration state: slot fields set, nested key
    # dropped, model columns NULLed (so model_hw is empty).
    migrated = _slot("s", "m", top_ngl=32, binary="rocmfpx", image_pin="ghcr.io/hal0ai/mine:v2")
    plan2 = _plan([migrated], {})
    assert plan2.slot_folds == []
    assert plan2.model_nulls == []


# ── applier: dry-run + deploy-window gate + real file surgery ──────────────────


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    from hal0.config import paths

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "s.toml").write_text('name = "s"\n[model]\ndefault = "m"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)
    monkeypatch.setattr(paths, "profiles_toml", lambda: tmp_path / "profiles.toml")

    plan = UnfoldPlan(slot_folds=[SlotFold("s", set_ngl=32)])
    lines = apply_unfold_plan(plan, dry_run=True)
    assert any("would fold" in ln for ln in lines)
    # untouched
    assert "n_gpu_layers" not in (slots_dir / "s.toml").read_text(encoding="utf-8")


def test_write_requires_deploy_window_ack():
    plan = UnfoldPlan(slot_folds=[SlotFold("s", set_ngl=32)])
    with pytest.raises(DeployWindowRequired):
        apply_unfold_plan(plan, deploy_window=False, dry_run=False)


def test_apply_surgery_and_idempotent_rerun(tmp_path, monkeypatch):
    from hal0.config import paths
    from hal0.config.migrations import hw_slot_ownership as mod

    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "s.toml").write_text(
        'name = "s"\nprofile = "p"\nimage = "ghcr.io/hal0ai/slotimg:v1"\n'
        '[model]\ndefault = "m"\nn_gpu_layers = 20\n',
        encoding="utf-8",
    )
    prof_path = tmp_path / "profiles.toml"
    prof_path.write_text(
        '[profile.p]\nimage = "ghcr.io/hal0ai/profimg:v1"\nflags = "-fa on"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "slots_config_dir", lambda: slots_dir)
    monkeypatch.setattr(paths, "profiles_toml", lambda: prof_path)

    # Stub the DB null-out so the surgery test stays DB-free; record the ids.
    nulled: list = []
    monkeypatch.setattr(mod, "_null_model_hw_columns", lambda ids, **kw: nulled.extend(ids))

    slots = [
        {
            "name": "s",
            "profile": "p",
            "image": "ghcr.io/hal0ai/slotimg:v1",
            "model": {"default": "m", "n_gpu_layers": 20},
        }
    ]
    model_hw = {"m": ModelHw(99, "rocmfpx")}
    profile_images = {"p": "ghcr.io/hal0ai/profimg:v1"}

    plan = plan_unfold(slots, model_hw, profile_images, stale_refs=_STALE)
    apply_unfold_plan(plan, deploy_window=True, dry_run=False)

    slot_after = tomllib.loads((slots_dir / "s.toml").read_text(encoding="utf-8"))
    # NGL: slot's own nested 20 wins over the model column's 99, promoted to top.
    assert slot_after["n_gpu_layers"] == 20
    assert "n_gpu_layers" not in slot_after["model"]  # nested sunset
    assert slot_after["binary"] == "rocmfpx"
    # slot's own image wins over the profile pin → image_pin, image collapsed.
    assert slot_after["image_pin"] == "ghcr.io/hal0ai/slotimg:v1"
    assert "image" not in slot_after
    # profile image deleted.
    prof_after = tomllib.loads(prof_path.read_text(encoding="utf-8"))
    assert "image" not in prof_after["profile"]["p"]
    assert "m" in nulled

    # ── idempotent re-run: re-collect from the written files, re-plan → no-op.
    reloaded = tomllib.loads((slots_dir / "s.toml").read_text(encoding="utf-8"))
    reloaded.setdefault("name", "s")
    plan2 = plan_unfold([reloaded], {}, {}, stale_refs=_STALE)
    assert plan2.slot_folds == []
    assert plan2.model_nulls == []
