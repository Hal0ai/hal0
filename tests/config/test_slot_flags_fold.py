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
    FoldPartiallyApplied,
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


# ── per-model isolation: an unregistered model skips, it does not abort (#2180) ──


class _RegistryWithout(_FakeRegistry):
    """A fake whose ``update`` raises like the real store for unknown ids."""

    def __init__(self, missing: set[str]) -> None:
        """Record writes; raise ModelNotFound for ids in ``missing``."""
        super().__init__()
        self.missing = missing

    def update(self, model_id: str, updates: dict) -> None:
        """Raise like the real store for a missing id, else record."""
        if model_id in self.missing:
            from hal0.registry.store import ModelNotFound

            raise ModelNotFound(f"model {model_id!r} not in registry")
        super().update(model_id, updates)


def _three_slot_plan():
    """Plan for slots one/two/three on a-model/b-ghost/c-model (all keyed)."""
    # Folds apply in model-id order, so the unregistered model sits in the
    # MIDDLE: before #2180 the first model was written and the third never was.
    return plan_slot_flags_fold(
        [
            _slot("one", "a-model", extra_args="-fa on"),
            _slot("two", "b-ghost", extra_args="-b 512"),
            _slot("three", "c-model", extra_args="-b 2048"),
        ],
        {"rocm": ""},
        {"a-model": None, "b-ghost": None, "c-model": None},
    )


def test_unregistered_model_is_skipped_and_the_rest_still_fold():
    """A write-time ModelNotFound skips that model; the others still fold."""
    reg = _RegistryWithout({"b-ghost"})
    with pytest.raises(FoldPartiallyApplied) as exc:
        apply_fold_plan(_three_slot_plan(), reg, deploy_window=True, dry_run=False)

    assert [m for m, _u in reg.updates] == ["a-model", "c-model"]
    [skip] = exc.value.skipped
    assert skip.model_id == "b-ghost"
    assert skip.slot_names == ("two",)
    assert "not in registry" in skip.reason
    # The report still carries every applied fold plus the named skip.
    assert any("'a-model'" in ln for ln in exc.value.lines)
    assert any("'c-model'" in ln for ln in exc.value.lines)
    assert any("b-ghost" in ln and "two" in ln for ln in exc.value.lines)


def test_two_of_three_unregistered_both_skip_and_the_third_folds():
    """Several misses are all collected; the one registered model folds."""
    reg = _RegistryWithout({"a-model", "b-ghost"})
    with pytest.raises(FoldPartiallyApplied) as exc:
        apply_fold_plan(_three_slot_plan(), reg, deploy_window=True, dry_run=False)

    assert [m for m, _u in reg.updates] == ["c-model"]
    assert [(s.model_id, s.slot_names) for s in exc.value.skipped] == [
        ("a-model", ("one",)),
        ("b-ghost", ("two",)),
    ]


def test_skip_on_the_last_fold_still_signals_partial():
    """A miss on the final fold still raises the partial result."""
    reg = _RegistryWithout({"c-model"})
    with pytest.raises(FoldPartiallyApplied) as exc:
        apply_fold_plan(_three_slot_plan(), reg, deploy_window=True, dry_run=False)

    assert [m for m, _u in reg.updates] == ["a-model", "b-ghost"]
    assert [(s.model_id, s.slot_names) for s in exc.value.skipped] == [("c-model", ("three",))]
    assert exc.value.lines[-1] == "SKIP model 'c-model' <- slots=['three']: not in registry"


def test_all_registered_models_fold_without_a_partial_signal():
    """With every model registered the applier returns normally."""
    reg = _RegistryWithout(set())
    lines = apply_fold_plan(_three_slot_plan(), reg, deploy_window=True, dry_run=False)
    assert [m for m, _u in reg.updates] == ["a-model", "b-ghost", "c-model"]
    assert len([ln for ln in lines if ln.startswith("fold ")]) == 3


def test_unexpected_write_error_is_not_treated_as_a_skip():
    """Only ModelNotFound is a skip; any other write error propagates."""

    class _Broken(_FakeRegistry):
        """Registry whose write for b-ghost fails with an OSError."""

        def update(self, model_id: str, updates: dict) -> None:
            """Fail the b-ghost write, record the rest."""
            if model_id == "b-ghost":
                raise OSError("disk full")
            super().update(model_id, updates)

    reg = _Broken()
    with pytest.raises(OSError, match="disk full"):
        apply_fold_plan(_three_slot_plan(), reg, deploy_window=True, dry_run=False)


# ── unregistered models are classified before no-op / divergence pruning ──────


def test_unregistered_model_with_an_empty_tune_is_named_not_noop():
    """A binding with nothing to fold must still be reported as unregistered,
    not as "already folded" — otherwise --apply exits 0 and never names it."""
    plan = plan_slot_flags_fold(
        [_slot("one", "a-model", extra_args="-fa on"), _slot("two", "b-ghost")],
        {"rocm": ""},
        {"a-model": None},
    )
    assert plan.skipped == []

    preview = apply_fold_plan(plan, _FakeRegistry(), dry_run=True)
    assert "SKIP model 'b-ghost' <- slots=['two']: not in registry" in preview

    reg = _FakeRegistry()
    with pytest.raises(FoldPartiallyApplied) as exc:
        apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert [m for m, _u in reg.updates] == ["a-model"]
    assert [(s.model_id, s.slot_names) for s in exc.value.skipped] == [("b-ghost", ("two",))]


def test_divergent_tunes_on_an_unregistered_model_skip_instead_of_refusing_the_run():
    """Divergence on a model that cannot be written anyway must not block every
    other slot: it is a skip, and the registered model still folds."""
    plan = plan_slot_flags_fold(
        [
            _slot("a", "ghost", extra_args="-b 512"),
            _slot("b", "ghost", extra_args="-b 999"),
            _slot("c", "m", extra_args="-fa on"),
        ],
        {"rocm": ""},
        {"m": None},
    )
    assert plan.ok

    reg = _FakeRegistry()
    with pytest.raises(FoldPartiallyApplied) as exc:
        apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)
    assert [m for m, _u in reg.updates] == ["m"]
    assert [(s.model_id, s.slot_names) for s in exc.value.skipped] == [("ghost", ("a", "b"))]


def test_provider_lane_registry_miss_is_an_informational_skip_not_partial():
    """A slot that does not launch through llama-server never reads the folded
    tune, so a missing row there is not outstanding work (#2324)."""
    tts = {**_slot("voice", "qwen3-tts", extra_args="--default_voice Ryan"), "type": "tts"}
    plan = plan_slot_flags_fold(
        [_slot("one", "a-model", extra_args="-fa on"), tts],
        {"rocm": ""},
        {"a-model": None},
        is_provider_lane=lambda cfg: cfg.get("type") == "tts",
    )
    assert plan.missing == []

    reg = _FakeRegistry()
    lines = apply_fold_plan(plan, reg, deploy_window=True, dry_run=False)  # no raise
    assert [m for m, _u in reg.updates] == ["a-model"]
    assert "skip model 'qwen3-tts' <- slots=['voice']: provider-lane, no registry row" in lines


def test_lane_classification_is_per_slot_not_per_display_name():
    """Two slot files can share a display name (a half-applied rename, or an
    id-keyed 10.toml/11.toml pair). A provider-lane "voice" must not vouch
    for a llama-server "voice" whose model is unregistered."""
    lane_voice = {**_slot("voice", "ghost"), "type": "tts"}
    llama_voice = _slot("voice", "ghost", extra_args="-b 2048")
    plan = plan_slot_flags_fold(
        [lane_voice, llama_voice],
        {"rocm": ""},
        {},
        is_provider_lane=lambda cfg: cfg.get("type") == "tts",
    )
    assert plan.lane_skips == []
    assert [(m.model_id, m.slot_names) for m in plan.missing] == [("ghost", ("voice", "voice"))]


def test_dry_run_classification_never_persists_a_legacy_profile(tmp_hal0_home: str) -> None:
    """The classifier runs under dry-run (the updater probe, the CLI preview).
    Resolving a shipped legacy profile through ProfileCatalog.resolve() would
    write profiles.toml on a fresh box (_materialize_legacy); it must not."""
    from hal0.config import paths
    from hal0.config.migrations.slot_flags_fold import run_migration

    slots = paths.slots_config_dir()
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "agent.toml").write_text(
        'name = "agent"\ntype = "llm"\nport = 8081\nprofile = "chadrock-moe"\n'
        '[model]\ndefault = "ghost"\n[server]\nextra_args = "-b 2048"\n',
        encoding="utf-8",
    )
    assert not paths.profiles_toml().exists()

    lines = run_migration(dry_run=True)

    assert not paths.profiles_toml().exists()
    # chadrock-moe is a llama-server profile, so the miss is ordinary work.
    assert "SKIP model 'ghost' <- slots=['agent']: not in registry" in lines
