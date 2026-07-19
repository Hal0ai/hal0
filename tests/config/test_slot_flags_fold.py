"""Tests for the FLAGS-own one-shot migrator (spec-flags-ownership §5).

Covers: sole-slot fold, managed-flag split (-ngl/-c → typed fields, never
extra_args), consensus multi-slot fold, divergent-share REFUSAL, idempotence,
dry-run (writes nothing), and the deploy-window write gate.
"""

from __future__ import annotations

import pytest

from hal0.config.migrations.slot_flags_fold import (
    DeployWindowRequired,
    FoldedTune,
    apply_fold_plan,
    compute_folded_tune,
    plan_slot_flags_fold,
)


class _FakeRegistry:
    """Minimal ModelRegistry stand-in recording .update() calls."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def update(self, model_id: str, updates: dict) -> None:
        self.updates.append((model_id, updates))


def _slot(name: str, model: str, **kw) -> dict:
    return {
        "name": name,
        "profile": kw.get("profile", "rocm"),
        "model": {
            "default": model,
            **({"n_gpu_layers": kw["ngl"]} if "ngl" in kw else {}),
            **({"context_size": kw["ctx"]} if "ctx" in kw else {}),
        },
        "server": {"extra_args": kw.get("extra_args", "")},
        **({"parallel": kw["parallel"]} if "parallel" in kw else {}),
    }


# ── managed-flag split ────────────────────────────────────────────────────────


def test_managed_flags_split_out_of_extra_args():
    """-ngl / -c NEVER land in extra_args (the launch denylist rejects -ngl on
    the screened model_extra_args segment) — they fold into the typed fields."""
    ft = compute_folded_tune(
        _slot("s", "m", ngl=30, ctx=16384, extra_args="-fa on -b 2048"),
        profile_flags="-ngl 999 -c 4096 -ub 4096",
        model_defaults=None,
    )
    assert ft.n_gpu_layers == 30  # slot beats profile's 999
    assert ft.context_size == 16384  # slot beats profile's 4096
    assert "-ngl" not in (ft.extra_args or "")
    assert "-c " not in (ft.extra_args or "") and "--ctx-size" not in (ft.extra_args or "")
    assert "-fa" in ft.extra_args and "-ub" in ft.extra_args


def test_parallel_folds_with_kv_unified():
    ft = compute_folded_tune(
        _slot("s", "m", parallel=8), profile_flags="-fa on", model_defaults=None
    )
    assert "--parallel 8" in ft.extra_args
    assert "--kv-unified" in ft.extra_args


# ── sole-slot fold ────────────────────────────────────────────────────────────


def test_sole_slot_auto_folds_with_provenance():
    plan = plan_slot_flags_fold(
        [_slot("primary", "qwen3-4b", ngl=99, extra_args="-fa on")],
        {"rocm": "-b 2048"},
        {"qwen3-4b": {"extra_args": None}},
    )
    assert not plan.refusals
    assert len(plan.folds) == 1
    fold = plan.folds[0]
    assert fold.model_id == "qwen3-4b"
    assert fold.source_profile == "rocm"  # provenance recorded
    assert fold.new_defaults["n_gpu_layers"] == 99
    assert "-b" in fold.new_defaults["extra_args"] and "-fa" in fold.new_defaults["extra_args"]


def test_slot_without_model_is_ignored():
    plan = plan_slot_flags_fold(
        [{"name": "orphan", "profile": "rocm", "server": {"extra_args": "-fa on"}}],
        {"rocm": ""},
        {},
    )
    assert not plan.folds and not plan.refusals


# ── divergent-share refusal ───────────────────────────────────────────────────


def test_divergent_share_is_refused_not_folded():
    slots = [
        _slot("a", "shared", extra_args="-b 512"),
        _slot("b", "shared", extra_args="-b 2048"),
    ]
    plan = plan_slot_flags_fold(slots, {"rocm": ""}, {"shared": None})
    assert plan.folds == []
    assert len(plan.refusals) == 1
    ref = plan.refusals[0]
    assert ref.model_id == "shared"
    assert set(ref.slot_tunes) == {"a", "b"}
    assert not plan.ok


def test_consensus_multi_slot_folds_once():
    """Multiple slots that fold to the IDENTICAL tune auto-fold (not divergent)."""
    slots = [
        _slot("a", "shared", extra_args="-fa on"),
        _slot("b", "shared", extra_args="-fa on"),
    ]
    plan = plan_slot_flags_fold(slots, {"rocm": ""}, {"shared": None})
    assert not plan.refusals
    assert len(plan.folds) == 1
    assert set(plan.folds[0].slot_names) == {"a", "b"}


def test_apply_refuses_whole_run_on_divergence():
    plan = plan_slot_flags_fold(
        [_slot("a", "m", extra_args="-b 512"), _slot("b", "m", extra_args="-b 999")],
        {"rocm": ""},
        {"m": None},
    )
    reg = _FakeRegistry()
    with pytest.raises(RuntimeError, match="divergent"):
        apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert reg.updates == []  # nothing written


# ── idempotence ───────────────────────────────────────────────────────────────


def test_idempotent_rerun_is_noop():
    slot = _slot("s", "m", ngl=40, extra_args="-fa on")
    pf = {"rocm": "-b 2048"}
    plan = plan_slot_flags_fold([slot], pf, {"m": None})
    folded = plan.folds[0].new_defaults
    # Re-plan with the model already carrying the folded defaults → no-op.
    plan2 = plan_slot_flags_fold([slot], pf, {"m": folded})
    assert plan2.folds == []
    assert any("no-op" in reason for _mid, reason in plan2.skipped)


# ── dry-run + deploy-window gate ──────────────────────────────────────────────


def test_dry_run_writes_nothing():
    plan = plan_slot_flags_fold([_slot("s", "m", extra_args="-fa on")], {"rocm": ""}, {"m": None})
    reg = _FakeRegistry()
    lines = apply_fold_plan(plan, reg, dry_run=True)
    assert reg.updates == []
    assert any("would fold" in ln for ln in lines)


def test_write_requires_deploy_window_ack():
    plan = plan_slot_flags_fold([_slot("s", "m", extra_args="-fa on")], {"rocm": ""}, {"m": None})
    reg = _FakeRegistry()
    with pytest.raises(DeployWindowRequired):
        apply_fold_plan(plan, reg, deploy_window=False, dry_run=False)
    assert reg.updates == []


def test_apply_writes_with_deploy_window():
    plan = plan_slot_flags_fold(
        [_slot("s", "m", ngl=50, extra_args="-fa on")], {"rocm": "-b 2048"}, {"m": None}
    )
    reg = _FakeRegistry()
    apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert len(reg.updates) == 1
    model_id, updates = reg.updates[0]
    assert model_id == "m"
    assert updates["defaults"]["n_gpu_layers"] == 50
    assert "-fa" in updates["defaults"]["extra_args"]


def test_folded_tune_equality_drives_divergence():
    """Sanity: FoldedTune value-equality is what the divergent check compares."""
    a = FoldedTune(extra_args="-b 2048", n_gpu_layers=1, context_size=None)
    b = FoldedTune(extra_args="-b 2048", n_gpu_layers=1, context_size=None)
    c = FoldedTune(extra_args="-b 512", n_gpu_layers=1, context_size=None)
    assert a == b and a != c
