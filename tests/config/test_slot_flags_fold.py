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
    """-ngl NEVER lands in extra_args (the launch denylist rejects -ngl on the
    screened model_extra_args segment) — it folds into the typed field. -c is
    stripped the same way but its value is DROPPED, not folded (a slot's own
    context_size always wins at launch — see FoldedTune docstring)."""
    ft = compute_folded_tune(
        _slot("s", "m", ngl=30, ctx=16384, extra_args="-fa on -b 2048"),
        profile_flags="-ngl 999 -c 4096 -ub 4096",
        model_defaults=None,
    )
    assert ft.n_gpu_layers == 30  # slot beats profile's 999
    assert not hasattr(ft, "context_size")
    assert "-ngl" not in (ft.extra_args or "")
    assert "-c " not in (ft.extra_args or "") and "--ctx-size" not in (ft.extra_args or "")
    assert "-fa" in ft.extra_args and "-ub" in ft.extra_args


def test_parallel_folds_with_kv_unified():
    ft = compute_folded_tune(
        _slot("s", "m", parallel=8), profile_flags="-fa on", model_defaults=None
    )
    assert "--parallel 8" in ft.extra_args
    assert "--kv-unified" in ft.extra_args


# ── chat_template fold (spec §7 slot-purity) ──────────────────────────────────


def test_chat_template_folds_into_model_defaults():
    """A slot's chat_template override materializes into model.defaults so the
    model owns the (model-intrinsic) template — spec §7."""
    plan = plan_slot_flags_fold(
        [{"name": "s", "model": {"default": "m"}, "chat_template": "qwen3"}],
        {},
        {"m": None},
    )
    assert not plan.refusals
    assert len(plan.folds) == 1
    assert plan.folds[0].new_defaults["chat_template"] == "qwen3"


def test_chat_template_auto_folds_to_nothing():
    """'auto'/absent normalize to None → no chat_template written, no refusal."""
    ft = compute_folded_tune(
        {"name": "s", "model": {"default": "m"}, "chat_template": "auto"},
        profile_flags="",
        model_defaults=None,
    )
    assert ft.chat_template is None
    assert "chat_template" not in ft.as_defaults_updates()


def test_slot_chat_template_beats_stale_model_default():
    """Slot override > existing model default (the old resolve precedence,
    now materialized by the fold rather than read at launch)."""
    ft = compute_folded_tune(
        {"name": "s", "model": {"default": "m"}, "chat_template": "qwen3"},
        profile_flags="",
        model_defaults={"chat_template": "chatml"},
    )
    assert ft.chat_template == "qwen3"


def test_divergent_chat_template_is_refused():
    """Two slots sharing one model with conflicting templates → refuse, don't
    silently pick one (spec §7 divergent-share refusal, same path as flags)."""
    slots = [
        {"name": "a", "model": {"default": "shared"}, "chat_template": "qwen3"},
        {"name": "b", "model": {"default": "shared"}, "chat_template": "chatml"},
    ]
    plan = plan_slot_flags_fold(slots, {}, {"shared": None})
    assert plan.folds == []
    assert len(plan.refusals) == 1
    ref = plan.refusals[0]
    assert ref.model_id == "shared"
    assert {t.chat_template for t in ref.slot_tunes.values()} == {"qwen3", "chatml"}
    assert not plan.ok


def test_auto_vs_absent_chat_template_is_not_divergent():
    """One slot with chat_template='auto' and one absent both normalize to None
    → consensus, not a spurious refusal."""
    slots = [
        {"name": "a", "model": {"default": "shared"}, "chat_template": "auto"},
        {"name": "b", "model": {"default": "shared"}},
    ]
    plan = plan_slot_flags_fold(slots, {}, {"shared": None})
    assert not plan.refusals
    # Neither slot contributes a template and the model has none → pure no-op.
    assert plan.folds == [] or all("chat_template" not in f.new_defaults for f in plan.folds)


def test_chat_template_fold_is_idempotent():
    """Re-running with the model already carrying the folded template → no-op."""
    slot = {"name": "s", "model": {"default": "m"}, "chat_template": "qwen3"}
    plan = plan_slot_flags_fold([slot], {}, {"m": None})
    folded = plan.folds[0].new_defaults
    plan2 = plan_slot_flags_fold([slot], {}, {"m": folded})
    assert plan2.folds == []
    assert any("no-op" in reason for _mid, reason in plan2.skipped)


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
    a = FoldedTune(extra_args="-b 2048", n_gpu_layers=1)
    b = FoldedTune(extra_args="-b 2048", n_gpu_layers=1)
    c = FoldedTune(extra_args="-b 512", n_gpu_layers=1)
    assert a == b and a != c


# ── ctx is launch-shadowed: no fold, no divergence key ─────────────────────────


def test_ctx_only_divergent_slots_do_not_refuse():
    """Slots that differ ONLY in [model].context_size must NOT refuse — a
    slot's own ctx always wins at launch (_resolve_context_size), so the
    model-level fold never sees it as a real disagreement. Regression for the
    halo143 qwen3.5-0.8b refusal: qtest(ctx4096) vs smoke(ctx8192)."""
    slots = [
        _slot("qtest", "qwen3.5-0.8b", ctx=4096, extra_args="-fa on"),
        _slot("smoke", "qwen3.5-0.8b", ctx=8192, extra_args="-fa on"),
    ]
    plan = plan_slot_flags_fold(slots, {"rocm": ""}, {"qwen3.5-0.8b": None})
    assert not plan.refusals
    assert plan.ok
    assert len(plan.folds) == 1
    fold = plan.folds[0]
    assert set(fold.slot_names) == {"qtest", "smoke"}
    assert "context_size" not in fold.new_defaults


def test_fold_omits_context_size_from_defaults():
    """Applying a fold must never write context_size into model.defaults —
    it has no launch effect (slot ctx always wins) so writing it is an
    unwanted clobber."""
    plan = plan_slot_flags_fold(
        [_slot("s", "m", ctx=16384, ngl=20, extra_args="-fa on")],
        {"rocm": ""},
        {"m": None},
    )
    reg = _FakeRegistry()
    apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert len(reg.updates) == 1
    _model_id, updates = reg.updates[0]
    assert "context_size" not in updates["defaults"]


def test_fold_reads_extra_args_reparked_under_extra_by_the_serializer():
    """#1396 regression: the live input shape parks `[server]` under `extra`.

    ``collect_inputs`` feeds the planner ``SlotConfig.model_dump(by_alias=True)``,
    and SlotConfig's ``_tuck_server_into_extra`` model_serializer re-parks the
    server sub-table under ``extra["server"]`` (so the loader round-trips a
    proper ``[server]`` TOML table). The planner previously read only a
    TOP-LEVEL ``server`` key, so against real input it silently dropped every
    slot's freeform extra_args — the single value this migrator exists to
    preserve.
    """
    reparked = {
        "name": "s",
        "type": "llm",
        "profile": "rocm",
        "model": {"default": "m"},
        "extra": {"server": {"extra_args": "-b 2048 -fa on"}},
    }
    plan = plan_slot_flags_fold([reparked], {"rocm": ""}, {"m": None})

    assert len(plan.folds) == 1
    tune = plan.folds[0].new_defaults["extra_args"]
    assert "-b 2048" in tune
    assert "-fa on" in tune


def test_divergent_share_is_detected_through_the_reparked_shape():
    """The dropped-extra_args bug also defeated the divergence guard.

    Two slots whose ONLY difference lived in the re-parked extra_args folded to
    an identical tune, so the planner saw no conflict and would have silently
    picked a winner instead of refusing.
    """
    a = {
        "name": "a",
        "type": "llm",
        "profile": "rocm",
        "model": {"default": "shared"},
        "extra": {"server": {"extra_args": "-b 2048"}},
    }
    b = {
        "name": "b",
        "type": "llm",
        "profile": "rocm",
        "model": {"default": "shared"},
        "extra": {"server": {"extra_args": "-b 512"}},
    }
    plan = plan_slot_flags_fold([a, b], {"rocm": ""}, {"shared": None})

    assert not plan.ok
    assert [r.model_id for r in plan.refusals] == ["shared"]
    assert plan.folds == []
