"""Divergent slot-profile overlay (#1636) — per-slot flag divergence.

PR #1635 removed the duplicate-for-device model flow; the divergence path is
now: a slot whose ``profile`` differs from the model's stamped provenance
(``defaults.profile``) launches with that profile's flags layered over the
model tune in an UNTRUSTED ``slot_profile`` segment:

    base < model_extra_args < slot_profile < slot_hardware < chat_template < mmproj

The ALIGNED case (slot profile == provenance, or no provenance at all) stays
byte-identical to golden #5 (tests/golden_paths/
test_gp05_stamped_launch_layering.py) — no ``slot_profile`` segment, no live
profile-flag read.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import _llama_argv_segments, _resolve_llama_scalars
from hal0.slots.argv import resolve_argv


class _NamedProfile(ProfileConfig):
    """ProfileConfig plus the ``name`` a ResolvedProfile carries."""

    name: str = ""


def _profile(name: str, flags: str) -> _NamedProfile:
    return _NamedProfile(name=name, flags=flags, mtp=False)


def _model(provenance: str | None, extra_args: str = "-b 2048") -> dict:
    defaults: dict = {"extra_args": extra_args}
    if provenance is not None:
        defaults["profile"] = provenance
    return {
        "_model_key": "qwen3-4b",
        "path": "/m/qwen3-4b.gguf",
        "tags": ["chat"],
        "defaults": defaults,
    }


def _slot(profile: str) -> dict:
    return {"name": "coder", "profile": profile, "port": 8082}


# ── divergence gate (_resolve_llama_scalars) ─────────────────────────────────


def test_divergent_profile_produces_flags():
    scalars = _resolve_llama_scalars(
        _slot("coding"), _model("chat"), _profile("coding", "--temp 0.7 --top-k 40")
    )
    assert scalars["slot_profile_flags"] == "--temp 0.7 --top-k 40"


def test_aligned_profile_produces_no_flags():
    scalars = _resolve_llama_scalars(_slot("chat"), _model("chat"), _profile("chat", "--temp 0.7"))
    assert scalars["slot_profile_flags"] == ""


def test_provenance_less_model_produces_no_flags():
    # A hand-authored tune records no profile choice — nothing to diverge from.
    scalars = _resolve_llama_scalars(
        _slot("coding"), _model(None), _profile("coding", "--temp 0.7")
    )
    assert scalars["slot_profile_flags"] == ""


def test_base_fallback_profile_produces_no_flags():
    # The slot names a profile that no longer resolves; the caller fell back to
    # the backend base profile. Its flags must NOT be injected — the operator
    # never picked them.
    scalars = _resolve_llama_scalars(
        _slot("retired-profile"), _model("chat"), _profile("rocm", "-fa on")
    )
    assert scalars["slot_profile_flags"] == ""


def test_legacy_flags_str_stays_empty():
    scalars = _resolve_llama_scalars(
        _slot("coding"), _model("chat"), _profile("coding", "--temp 0.7")
    )
    assert scalars["flags_str"] == ""


# ── segment emission + precedence (_llama_argv_segments) ─────────────────────


def _segments(slot_profile_flags: str = "", **kw):
    return _llama_argv_segments(
        port=8082,
        model_path="/m/qwen3-4b.gguf",
        model_defaults={"extra_args": "-b 2048 --temp 0"},
        slot_profile_flags=slot_profile_flags,
        **kw,
    )


def test_segment_emitted_between_model_tune_and_slot_hardware():
    labels = [label for label, _ in _segments("--temp 0.7", slot_n_gpu_layers=30)]
    assert labels.index("model_extra_args") < labels.index("slot_profile")
    assert labels.index("slot_profile") < labels.index("slot_hardware")


def test_no_segment_when_empty():
    labels = {label for label, _ in _segments("")}
    assert "slot_profile" not in labels


def test_divergent_profile_wins_collisions_with_model_tune():
    resolved = resolve_argv(_segments("--temp 0.7 --top-k 40"))
    prov = {p.flag: p.source for p in resolved.provenance}
    assert resolved.argv[resolved.argv.index("--temp") + 1] == "0.7"
    assert prov["--temp"] == "slot_profile"
    # Model-tune flags the profile does not mention survive underneath.
    assert "-b" in resolved.argv
    assert prov["-b"] == "model_extra_args"


def test_slot_hardware_still_wins_over_divergent_profile():
    # Defense in depth: profile saves reject hardware flags, but a hand-edited
    # profiles.toml can smuggle one — the typed slot field must win.
    resolved = resolve_argv(_segments("--threads 4", slot_threads=16))
    assert resolved.argv[resolved.argv.index("--threads") + 1] == "16"


def test_managed_flag_in_divergent_profile_fails_loudly():
    with pytest.raises(BadRequest):
        resolve_argv(_segments("--port 9999"))


# ── end-to-end scalars → segments parity ─────────────────────────────────────


def test_scalars_thread_into_segments():
    scalars = _resolve_llama_scalars(
        _slot("coding"), _model("chat"), _profile("coding", "--temp 0.7")
    )
    segments = _llama_argv_segments(
        port=8082,
        model_path="/m/qwen3-4b.gguf",
        model_defaults=scalars["model_defaults"],
        slot_profile_flags=scalars["slot_profile_flags"],
    )
    resolved = resolve_argv(segments)
    prov = {p.flag: p.source for p in resolved.provenance}
    assert prov["--temp"] == "slot_profile"
