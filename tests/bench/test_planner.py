"""test_planner.py — the staleness set-difference against a temp store (DESIGN §6).

Exercises both clauses: (1) a cell with no ok record is stale; once an ok record
with that exact cell_key is appended it is NOT stale; and (2) a cell whose newest
ok record is older than max_age_days is stale again. Also checks that a
provenance change (different resolved argv, expressed via the registry profile)
re-introduces staleness because the cell_key moved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hal0.bench.planner import (
    NO_GPU_LANE,
    _lane_is_plannable,
    _model_caps,
    _resolve_lane,
    _resolve_profile,
    plan,
)
from hal0.bench.schema import Host, Outcome, Record
from hal0.bench.store import Store
from hal0.bench.suites import suite_from_dict


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def _suite():
    return suite_from_dict(
        {
            "suite": {"id": "roster", "priority": 50},
            "selector": {"installed": True},
            "matrix": {"lanes": ["default"], "depths": [2048], "samplers": ["greedy"], "reps": 3},
            "cells": {"kinds": ["tg"]},
            "staleness": {"max_age_days": 30},
        }
    )


def _registry():
    # one installed model with a default lane hint and a resolved profile
    return [
        {
            "id": "m1",
            "installed": True,
            "caps": ["chat"],
            "sha256": "abc",
            "default_lane": "rocm",
            "profile": {"argv": ["-b", "512"], "kv": {"main_k": "q8_0"}, "ctx": 32768},
            "n_gen": 256,
        }
    ]


def _ok_record_for(cell, run_id: str) -> Record:
    return Record(
        run_id=run_id,
        suite="roster",
        trigger="manual",
        identity=cell.identity,
        host=Host(hal0_version="0.9.0"),
        outcome=Outcome.OK,
    )


def test_never_measured_is_stale(store):
    cells = plan(_suite(), _registry(), store)
    assert len(cells) == 1
    assert cells[0].reason == "never-measured"
    assert cells[0].model_id == "m1"
    assert cells[0].kind == "tg"


def test_ok_record_clears_staleness(store):
    cells = plan(_suite(), _registry(), store)
    # append a fresh ok record for exactly that cell_key
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{now}-aaa"))
    # re-plan: the set-difference now removes it
    assert plan(_suite(), _registry(), store) == []


def test_aged_record_is_stale_again(store):
    cells = plan(_suite(), _registry(), store)
    old_dt = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{old_dt}-old"))
    replanned = plan(_suite(), _registry(), store)
    assert len(replanned) == 1
    assert replanned[0].reason.startswith("stale:")


def test_failed_record_does_not_clear_staleness(store):
    cells = plan(_suite(), _registry(), store)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = _ok_record_for(cells[0], f"{now}-fail")
    rec.outcome = Outcome.FAILED  # only ok records count (DESIGN §6)
    store.append_record(rec)
    # store keys current cells off ok records only, so still stale
    assert len(plan(_suite(), _registry(), store)) == 1


def test_provenance_drift_reintroduces_staleness(store):
    cells = plan(_suite(), _registry(), store)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{now}-aaa"))
    assert plan(_suite(), _registry(), store) == []

    # a merged flag PR changes resolved argv -> different cell_key -> stale again
    drifted = _registry()
    drifted[0]["profile"]["argv"] = ["-b", "1024"]
    replanned = plan(_suite(), drifted, store)
    assert len(replanned) == 1
    assert replanned[0].reason == "never-measured"
    assert replanned[0].cell_key != cells[0].cell_key


def test_config_matrix_expands_to_distinct_cells(store):
    # each config variant with distinct flags is its own cell (its flags feed
    # cell_key), so a suite can A/B tuning flags and compare them.
    suite = suite_from_dict(
        {
            "suite": {"id": "cfg"},
            "selector": {"installed": True},
            "matrix": {
                "lanes": ["default"],
                "depths": [2048],
                "reps": 1,
                "configs": [
                    {"label": "default", "flags": {}},
                    {"label": "b1024", "flags": {"-b": 1024}},
                ],
            },
            "cells": {"kinds": ["tg"]},
        }
    )
    cells = plan(suite, _registry(), store)
    assert len(cells) == 2  # 1 model x 1 kind x 2 variants
    assert len({c.cell_key for c in cells}) == 2  # b1024 differs from default
    assert {c.config_label for c in cells} == {"default", "b1024"}
    b = next(c for c in cells if c.config_label == "b1024")
    assert b.flags == {"-b": "1024"}
    assert "-b" in b.identity.config.argv and "1024" in b.identity.config.argv


def test_config_non_whitelisted_flag_dropped(store):
    # a non-seam-whitelisted flag is dropped (the seam would reject it); the rest
    # of the variant's flags still apply.
    suite = suite_from_dict(
        {
            "suite": {"id": "cfg"},
            "selector": {"installed": True},
            "matrix": {
                "lanes": ["default"],
                "depths": [2048],
                "reps": 1,
                "configs": [{"label": "mix", "flags": {"--nope": 1, "-ctk": "q8_0"}}],
            },
            "cells": {"kinds": ["tg"]},
        }
    )
    cells = plan(suite, _registry(), store)
    assert len(cells) == 1
    assert cells[0].flags == {"-ctk": "q8_0"}  # --nope dropped, -ctk kept


def test_selector_excludes_uninstalled(store):
    reg = _registry()
    reg[0]["installed"] = False
    assert plan(_suite(), reg, store) == []


# --------------------------------------------------------------------------- #
# #1773 — "chat" cell tokenizer resolution (plan-time gate)
# --------------------------------------------------------------------------- #


def _chat_suite():
    return suite_from_dict(
        {
            "suite": {"id": "chat-suite", "priority": 50},
            "selector": {"installed": True},
            "matrix": {"lanes": ["default"], "depths": [2048], "samplers": ["greedy"], "reps": 1},
            "cells": {"kinds": ["chat"]},
            "staleness": {"max_age_days": 30},
        }
    )


class TestChatTokenizerGate:
    """A ``chat`` cell drives GuideLLM, which needs a real HF repo id for
    ``--tokenizer`` — a local-only model id (e.g. a GGUF slot name) always
    fails that resolution mid-session (issue #1773). The planner must
    reject such a model at PLAN time, mirroring the missing-adapter-tool
    gate (test_runner.py's ``test_missing_adapter_tool_is_rejected_at_plan_
    time``), rather than letting the runner discover the gap per-cell."""

    @pytest.fixture(autouse=True)
    def _adapter_tool_present(self, monkeypatch):
        import hal0.bench.planner as planner_mod

        monkeypatch.setattr(planner_mod.adapters, "resolve_tool", lambda _name: "/usr/bin/true")

    def test_local_only_model_rejected_at_plan_time(self, store):
        # No defaults.tokenizer_repo AND no hf_repo — nothing trustworthy to
        # resolve --tokenizer from.
        reg = [
            {
                "id": "chadrock-35b-ace-saber-rocmfp4-mtp",
                "installed": True,
                "caps": ["chat"],
                "default_lane": "rocm",
            }
        ]
        with pytest.raises(ValueError, match="tokenizer_repo") as exc_info:
            plan(_chat_suite(), reg, store)
        # Actionable: names the offending model id, not just "some model".
        assert "chadrock-35b-ace-saber-rocmfp4-mtp" in str(exc_info.value)

    def test_hf_repo_alone_is_a_trustworthy_source(self, store):
        reg = [
            {
                "id": "m1",
                "installed": True,
                "caps": ["chat"],
                "default_lane": "rocm",
                "hf_repo": "Qwen/Qwen3-4B-GGUF",
            }
        ]
        cells = plan(_chat_suite(), reg, store)
        assert len(cells) == 1
        assert cells[0].tokenizer == "Qwen/Qwen3-4B-GGUF"

    def test_explicit_tokenizer_repo_wins_over_hf_repo(self, store):
        reg = [
            {
                "id": "m1",
                "installed": True,
                "caps": ["chat"],
                "default_lane": "rocm",
                "hf_repo": "Qwen/Qwen3-4B-GGUF",
                "tokenizer_repo": "meta-llama/Llama-3.1-8B",
            }
        ]
        cells = plan(_chat_suite(), reg, store)
        assert len(cells) == 1
        assert cells[0].tokenizer == "meta-llama/Llama-3.1-8B"

    def test_non_chat_cell_never_needs_a_tokenizer(self, store):
        # A "tg" suite over the same local-only model must plan fine — the
        # gate is scoped to suites that actually include "chat".
        reg = [
            {
                "id": "chadrock-35b-ace-saber-rocmfp4-mtp",
                "installed": True,
                "caps": ["chat"],
                "default_lane": "rocm",
            }
        ]
        cells = plan(_suite(), reg, store)  # module-level _suite() plans "tg"
        assert len(cells) == 1
        assert cells[0].tokenizer == ""


class TestModelCapsTypedFields:
    """`_model_caps` folds the TYPED registry fields in alongside the freeform
    lists (#1823).

    The freeform `capabilities`/`tags` are unmaintained in practice, while the
    typed fields are what `PATCH /api/models` actually edits — and this set is
    what stamps `identity.model.caps` onto every benchmark record, so it drives
    the public leaderboard's capability pills.

    Every typed flag is tri-state: None means "unset / decided elsewhere",
    never "lacks the capability".
    """

    def test_freeform_lists_still_win_on_their_own(self):
        assert _model_caps({"capabilities": ["chat"], "tags": ["coder"]}) == {"chat", "coder"}

    def test_mmproj_presence_is_the_vision_signal(self):
        # The projector is a model fact: carrying one means the model can see.
        # Whether a slot loads it is its profile's `vision` — a tune, not a cap.
        assert "vision" in _model_caps({"mmproj": "/models/x/mmproj.gguf"})

    def test_a_profile_vision_off_does_not_revoke_the_model_fact(self):
        caps = _model_caps({"mmproj": "/models/x/mmproj.gguf", "profile": {"vision": "off"}})
        assert "vision" in caps

    def test_no_projector_means_no_vision(self):
        assert "vision" not in _model_caps({"defaults": {}})
        assert "vision" not in _model_caps({"profile": {"vision": "auto"}})

    def test_tool_calling_flag_folds_in(self):
        assert "tool-calling" in _model_caps({"capability_flags": {"tool_calling": True}})

    def test_profile_enable_thinking_true_implies_reasoning(self):
        # The thinking default is a profile tune; the planner reads it from the
        # profile the caller attached to the registry entry.
        assert "reasoning" in _model_caps({"profile": {"enable_thinking": True}})

    def test_model_defaults_no_longer_carry_a_reasoning_signal(self):
        assert "reasoning" not in _model_caps({"defaults": {"enable_thinking": True}})

    @pytest.mark.parametrize("value", [None, False])
    def test_tri_state_none_and_false_assert_nothing(self, value):
        caps = _model_caps(
            {
                "capability_flags": {"tool_calling": value},
                "defaults": {"mtp": value},
                "profile": {"enable_thinking": value},
            }
        )
        assert caps == set()

    def test_missing_tables_do_not_explode(self):
        assert _model_caps({}) == set()
        assert _model_caps({"defaults": None, "capability_flags": None}) == set()

    def test_typed_and_freeform_union_rather_than_replace(self):
        caps = _model_caps(
            {
                "capabilities": ["chat"],
                "tags": ["coder"],
                "mmproj": "/m/mmproj.gguf",
                "capability_flags": {"tool_calling": True},
                "mtp": True,
                "profile": {"enable_thinking": True},
            }
        )
        assert caps == {"chat", "coder", "vision", "tool-calling", "mtp", "reasoning"}

    def test_widening_can_only_add_caps_any_matches(self):
        """Suites match with caps_any, so a wider set never de-selects a model
        that already matched — the property that makes this safe to land."""
        narrow = _model_caps({"capabilities": ["chat"]})
        wide = _model_caps({"capabilities": ["chat"], "capability_flags": {"tool_calling": True}})
        assert narrow <= wide


class TestResolveProfile:
    """The tune a cell is measured under comes from the attached profile (ADR-0029),
    never from the model row: a model holds file facts only."""

    def test_profile_flags_string_becomes_argv_and_ctx_size_the_window(self):
        cfg = _resolve_profile(
            {"id": "m", "profile": {"flags": "-b 1024 -ctk q8_0 -fa on", "ctx_size": 65536}}, 4096
        )
        assert cfg.argv == ["-b", "1024", "-ctk", "q8_0", "-fa", "on"]
        assert cfg.ctx == 65536

    def test_structured_synthetic_profile_still_wins_its_argv(self):
        cfg = _resolve_profile({"id": "m", "profile": {"argv": ["-b", "512"], "ctx": 8192}}, 4096)
        assert cfg.argv == ["-b", "512"]
        assert cfg.ctx == 8192

    def test_no_profile_benches_runner_defaults_at_the_cell_depth(self):
        cfg = _resolve_profile({"id": "m"}, 4096)
        assert cfg.argv == []
        assert cfg.ctx == 4096

    def test_model_defaults_tune_is_ignored(self):
        cfg = _resolve_profile(
            {"id": "m", "defaults": {"extra_args": "-b 9999", "context_size": 99999}}, 4096
        )
        assert cfg.argv == []
        assert cfg.ctx == 4096

    def test_unbalanced_quote_in_flags_degrades_to_whitespace_split(self):
        cfg = _resolve_profile({"id": "m", "profile": {"flags": "-b 512 --x 'oops"}}, 2048)
        assert cfg.argv[:2] == ["-b", "512"]

    def test_a_changed_profile_flag_moves_the_cell_key(self, tmp_path):
        """DESIGN §6: a merged profile-flag change re-benches exactly that cell."""
        from hal0.bench.planner import _build_identity
        from hal0.bench.schema import cell_key

        base = {"id": "m", "default_lane": "rocm", "profile": {"flags": "-b 512"}}
        tuned = {"id": "m", "default_lane": "rocm", "profile": {"flags": "-b 1024"}}
        a = cell_key(_build_identity(base, "rocm", "tg", 2048, "greedy"))
        b = cell_key(_build_identity(tuned, "rocm", "tg", 2048, "greedy"))
        again = cell_key(_build_identity(base, "rocm", "tg", 2048, "greedy"))
        assert a == again
        assert a != b


class TestResolveLaneWithoutRocm:
    """#2511: the default lane must not be ``rocm`` on a box with no ROCm lane."""

    @staticmethod
    def _box(monkeypatch, *, rocm: bool, vulkan: bool, render_node: bool = True) -> None:
        monkeypatch.setattr("hal0.providers._gpu.rocm_lane_present", lambda *a, **k: rocm)
        monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: render_node)
        monkeypatch.setattr(
            "hal0.bench.harness.lane_is_supported",
            lambda lane: vulkan if lane == "vulkan_radv" else True,
        )

    def test_rocm_present_keeps_rocm_for_vulkan_backend(self, monkeypatch):
        self._box(monkeypatch, rocm=True, vulkan=True)
        assert _resolve_lane({"backends": ["vulkan"]}, "default") == "rocm"
        assert _resolve_lane({"default_lane": "vulkan"}, "default") == "rocm"

    def test_rocm_present_keeps_rocm_with_no_hints(self, monkeypatch):
        self._box(monkeypatch, rocm=True, vulkan=True)
        assert _resolve_lane({}, "default") == "rocm"

    def test_rocm_absent_vulkan_backend_resolves_to_vulkan(self, monkeypatch):
        self._box(monkeypatch, rocm=False, vulkan=True)
        assert _resolve_lane({"backends": ["vulkan"]}, "default") == "vulkan_radv"
        assert _resolve_lane({"default_lane": "vulkan"}, "default") == "vulkan_radv"

    def test_rocm_absent_no_hints_is_a_supported_lane(self, monkeypatch):
        self._box(monkeypatch, rocm=False, vulkan=True)
        assert _resolve_lane({}, "default") == "vulkan_radv"
        self._box(monkeypatch, rocm=False, vulkan=False)
        assert _resolve_lane({}, "default") == NO_GPU_LANE

    def test_no_render_node_with_a_vulkan_image_is_not_a_vulkan_lane(self, monkeypatch):
        """B2: the image check alone must not pick vulkan_radv."""
        self._box(monkeypatch, rocm=False, vulkan=True, render_node=False)
        assert _resolve_lane({"backends": ["vulkan"]}, "default") == NO_GPU_LANE
        assert _resolve_lane({}, "default") == NO_GPU_LANE

    def test_no_usable_gpu_lane_is_skipped_loudly(self, monkeypatch, capsys):
        """B3: no cpu fallback; the existing loud-skip path explains it."""
        self._box(monkeypatch, rocm=False, vulkan=True, render_node=False)
        assert _lane_is_plannable(NO_GPU_LANE, suite_id="roster") is False
        err = capsys.readouterr().err
        assert "no usable GPU lane" in err and "roster" in err

    def test_real_lanes_stay_plannable(self, monkeypatch):
        self._box(monkeypatch, rocm=True, vulkan=True)
        assert _lane_is_plannable("rocm", suite_id="roster") is True

    def test_explicit_lane_is_unchanged(self, monkeypatch):
        self._box(monkeypatch, rocm=False, vulkan=False)
        assert _resolve_lane({"backends": ["vulkan"]}, "rocm") == "rocm"
        assert _resolve_lane({}, "vulkan_radv") == "vulkan_radv"
        assert _resolve_lane({"default_lane": "rocm"}, "default") == "rocm"


def test_cell_spec_carries_a_non_default_speculation():
    from hal0.bench.planner import _resolve_profile

    plain = _resolve_profile({"profile": {"flags": "-fa on"}}, 4096)
    off = _resolve_profile({"profile": {"flags": "-fa on", "speculation": "off"}}, 4096)
    default = _resolve_profile({"profile": {"flags": "-fa on", "speculation": "auto"}}, 4096)
    assert off.spec == {"profile": {"speculation": "off"}}
    assert default.spec == plain.spec
