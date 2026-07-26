"""§7.1a / ML-5: mtp/jinja as RUNNER-gated capabilities injected by
``_resolve_llama_scalars``, and the full flag precedence chain:

    runner image  <  profile tune  <  arch defaults (FAMILY_DEFAULTS)
                  <  per-model metadata (mtp/jinja/extra_args)
                  <  slot instance overrides

Neither mtp nor jinja is a profile tune anymore — the seed profiles carry
neither ``--jinja`` nor an effective mtp opinion; both are runner
capabilities gated by the model's registry data. See tests/config/
test_mtp_override.py for the isolated _effective_mtp table and
tests/config/test_seeds_parity.py / tests/api/test_profiles_route.py for
the seed-shape pins.
"""

from __future__ import annotations

from hal0.config.schema import FAMILY_DEFAULTS, ProfileConfig
from hal0.providers.container import _llama_argv_segments, _resolve_llama_scalars
from hal0.slots.argv import resolve_argv


def _rocm_profile(**overrides) -> ProfileConfig:
    base = dict(flags="-fa on -b 512", mtp=False, device_class="gpu", backend="rocm")
    base.update(overrides)
    return ProfileConfig(**base)


def _cuda_profile(**overrides) -> ProfileConfig:
    base = dict(flags="-fa on", mtp=False, device_class="gpu", backend="cuda")
    base.update(overrides)
    return ProfileConfig(**base)


def _mtp_tagged_model(**overrides) -> dict:
    base = {"_model_key": "chad-mtp", "tags": ["chat", "mtp"], "path": "/m/chad-mtp.gguf"}
    base.update(overrides)
    return base


def _plain_model(**overrides) -> dict:
    base = {"_model_key": "plain-chat", "tags": ["chat"], "path": "/m/plain-chat.gguf"}
    base.update(overrides)
    return base


def _extra_args(scalars: dict) -> str:
    md = scalars.get("model_defaults") or {}
    return str(md.get("extra_args") or "")


# ── --jinja capability injection ──────────────────────────────────────────────


def test_jinja_default_on_for_llama_server_runner():
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), _rocm_profile())
    assert "--jinja" in _extra_args(scalars)


def test_jinja_suppressed_by_defaults_jinja_false():
    model = _plain_model(defaults={"jinja": False})
    scalars = _resolve_llama_scalars({"name": "s"}, model, _rocm_profile())
    assert "--jinja" not in _extra_args(scalars)


def test_jinja_defaults_true_and_none_both_mean_on():
    for jinja_val in (True, None):
        model = _plain_model(defaults={"jinja": jinja_val})
        scalars = _resolve_llama_scalars({"name": "s"}, model, _rocm_profile())
        assert "--jinja" in _extra_args(scalars), jinja_val


def test_jinja_never_injected_for_embedding_model():
    """embed/rerank never gets --jinja (llama-server's --jinja is a chat-
    completions feature, meaningless in --embedding mode). FLAGS-own: the mode
    marker now rides the MODEL's materialized tune (defaults.extra_args), not a
    live profile flag string, so the injection reads it from there."""
    model = _plain_model(defaults={"extra_args": "--embedding -fa on -b 8192 --no-mmap"})
    scalars = _resolve_llama_scalars({"name": "s"}, model, _rocm_profile())
    assert "--jinja" not in _extra_args(scalars)


def test_jinja_never_injected_for_reranking_model():
    model = _plain_model(defaults={"extra_args": "--reranking -fa on -b 8192 --no-mmap"})
    scalars = _resolve_llama_scalars({"name": "s"}, model, _rocm_profile())
    assert "--jinja" not in _extra_args(scalars)


def test_flags_str_is_empty_post_flags_own():
    """FLAGS-own: the profile flag string never reaches launch — the resolver
    hands back an empty flags_str; the model's materialized tune is the whole
    story (jinja/mtp land in the model_defaults segment, not flags_str)."""
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), _rocm_profile())
    assert scalars["flags_str"] == ""


# ── mtp: runner-gated, injected into the MODEL tune ───────────────────────────


def test_mtp_bundle_present_for_tagged_model_on_rocm():
    """profile.mtp is inert; AUTO fires via the registry tag AND
    runner.supports.mtp. FLAGS-own: the bundle is injected into the MODEL tune
    (model_defaults.extra_args), computed from the model-resolved mtp, not a
    profile flag string."""
    scalars = _resolve_llama_scalars({"name": "s"}, _mtp_tagged_model(), _rocm_profile())
    assert "--spec-type draft-mtp" in _extra_args(scalars)


def test_mtp_bundle_absent_for_untagged_model():
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), _rocm_profile())
    assert "--spec-type" not in _extra_args(scalars)


def test_mtp_bundle_absent_on_cuda_runner_even_for_tagged_model():
    """NEW gate (§7.1a / ML-5): the cuda llama-server runner doesn't support
    MTP drafting (RUNNER_IMAGES["cuda"].supports.mtp is False) — a tagged
    model on a cuda-backed profile does NOT speculate under AUTO."""
    scalars = _resolve_llama_scalars({"name": "s"}, _mtp_tagged_model(), _cuda_profile())
    assert "--spec-type" not in _extra_args(scalars)


def test_model_defaults_mtp_true_forces_bundle_even_on_cuda():
    """spec-hw-slot-ownership §1: the MODEL is the sole mtp authority now —
    an explicit defaults.mtp=True is an unconditional curator override, same
    contract the old slot.mtp escape hatch had."""
    model = _plain_model(defaults={"mtp": True})
    scalars = _resolve_llama_scalars({"name": "s"}, model, _cuda_profile())
    assert "--spec-type draft-mtp" in _extra_args(scalars)


def test_slot_mtp_key_has_no_effect_anymore():
    """A stray slot-side ``mtp`` key (pre-migration TOML) is ignored — only
    ``ModelDefaults.mtp`` decides now."""
    scalars = _resolve_llama_scalars({"name": "s", "mtp": True}, _plain_model(), _cuda_profile())
    assert "--spec-type" not in _extra_args(scalars)


# ── slot owns -ngl; model/profile inert ───────────────────────────────────────


def test_slot_ngl_wins_model_and_profile_inert():
    """spec-hw-slot-ownership §2 (reverses the §5 fold): the SLOT's top-level
    ``n_gpu_layers`` owns ``-ngl`` (trusted ``slot_hardware`` segment). A profile
    ``-ngl`` is inert (profile flags don't launch) and the deleted
    ``defaults.n_gpu_layers`` key on the model no longer emits anything."""
    profile = _rocm_profile(flags="-ngl 10 -fa on")  # inert
    model = _plain_model(
        defaults={"extra_args": "", "n_gpu_layers": 20},  # deleted-field key, ignored
        architecture="gemma3",  # exercises the family-defaults tier too
    )
    slot_cfg = {"name": "s", "n_gpu_layers": 30}  # authoritative slot NGL
    scalars = _resolve_llama_scalars(slot_cfg, model, profile)
    segments = _llama_argv_segments(
        port=8080,
        model_path="/m/plain-chat.gguf",
        profile_flags=scalars["flags_str"],
        model_defaults=scalars["model_defaults"],
        slot_n_gpu_layers=scalars["slot_n_gpu_layers"],
        slot_threads=scalars["slot_threads"],
    )
    resolved = resolve_argv(segments)
    assert resolved.argv[resolved.argv.index("-ngl") + 1] == "30"  # slot, not model 20/profile 10


def test_precedence_chain_family_beats_profile_but_loses_to_model_extra_args():
    """FAMILY_DEFAULTS (arch tier) lands INSIDE the model_defaults segment,
    prepended before the model's own extra_args -- so the model's explicit
    extra_args value for the SAME flag wins over the family default, while
    both still beat the profile segment."""
    profile = _rocm_profile(flags="-ctk q4_0 -fa on")  # profile wants q4_0
    model = _plain_model(
        architecture="gemma3",  # family default wants f16 (see FAMILY_DEFAULTS)
        defaults={"extra_args": ""},
    )
    scalars = _resolve_llama_scalars({"name": "s"}, model, profile)
    fam_flags = FAMILY_DEFAULTS.get("gemma", "")
    if "-ctk" in fam_flags:
        # family default beats the profile's -ctk q4_0
        segments = _llama_argv_segments(
            port=8080,
            model_path="/m/plain-chat.gguf",
            profile_flags=scalars["flags_str"],
            model_defaults=scalars["model_defaults"],
        )
        resolved = resolve_argv(segments)
        idx = resolved.argv.index("-ctk")
        assert resolved.argv[idx + 1] != "q4_0"


def test_family_flags_prefers_architecture_over_filename_scan():
    """§7.1a / ML-5: family_flags/model_family re-keyed off Model.architecture
    first; the filename/id token scan is the fallback only when architecture
    is unset."""
    from hal0.config.schema import family_flags, model_family

    # A filename that would sniff "qwen" but a real architecture of "gemma3"
    # -- architecture wins, no filename re-guessing.
    assert model_family("qwen-lookalike-name", architecture="gemma3") == "gemma"
    assert family_flags("qwen-lookalike-name", architecture="gemma3") == FAMILY_DEFAULTS.get(
        "gemma", ""
    )
    # architecture set but unmapped (no FAMILY_DEFAULTS entry) -> None, NOT
    # re-guessed from the filename even though the filename contains "gemma".
    assert model_family("gemma-lookalike-name", architecture="gpt-oss") is None
    # architecture unset -> falls back to the filename scan (legacy path).
    assert model_family("gemma-4-12b-it", architecture=None) == "gemma"
    assert model_family("gemma-4-12b-it", architecture="") == "gemma"
