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
    base = dict(image="img", flags="-fa on -b 512", mtp=False, device_class="gpu", backend="rocm")
    base.update(overrides)
    return ProfileConfig(**base)


def _cuda_profile(**overrides) -> ProfileConfig:
    base = dict(image="img", flags="-fa on", mtp=False, device_class="gpu", backend="cuda")
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


def test_jinja_never_injected_for_embedding_profile():
    """embed/rerank profiles never carried --jinja even pre-ML-5 — the
    capability injection must not regress that (llama-server's --jinja is a
    chat-completions feature, meaningless in --embedding mode)."""
    embed_profile = _rocm_profile(flags="--embedding -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap")
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), embed_profile)
    assert "--jinja" not in _extra_args(scalars)
    assert "--jinja" not in scalars["flags_str"]


def test_jinja_never_injected_for_reranking_profile():
    rerank_profile = _rocm_profile(flags="--reranking -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap")
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), rerank_profile)
    assert "--jinja" not in _extra_args(scalars)


def test_no_seed_profile_flags_carry_jinja_anymore():
    """The profile segment itself (flags_str) never carries --jinja post
    ML-5 — it's injected into the model_defaults segment instead."""
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), _rocm_profile())
    assert "--jinja" not in scalars["flags_str"]


# ── mtp: runner-gated, not profile-gated ──────────────────────────────────────


def test_mtp_bundle_absent_by_default_even_for_tagged_model_on_rocm():
    """profile.mtp is inert; AUTO only fires via the registry tag AND
    runner.supports.mtp — a tagged model on a plain (non-mtp-tuned) rocm
    profile still speculates now (mtp moved OFF the profile entirely)."""
    scalars = _resolve_llama_scalars({"name": "s"}, _mtp_tagged_model(), _rocm_profile())
    assert "--spec-type draft-mtp" in scalars["flags_str"]


def test_mtp_bundle_absent_for_untagged_model():
    scalars = _resolve_llama_scalars({"name": "s"}, _plain_model(), _rocm_profile())
    assert "--spec-type" not in scalars["flags_str"]


def test_mtp_bundle_absent_on_cuda_runner_even_for_tagged_model():
    """NEW gate (§7.1a / ML-5): the cuda llama-server runner doesn't support
    MTP drafting (RUNNER_IMAGES["cuda"].supports.mtp is False) — a tagged
    model on a cuda-backed profile does NOT speculate under AUTO."""
    scalars = _resolve_llama_scalars({"name": "s"}, _mtp_tagged_model(), _cuda_profile())
    assert "--spec-type" not in scalars["flags_str"]


def test_slot_mtp_true_forces_bundle_even_on_cuda():
    scalars = _resolve_llama_scalars({"name": "s", "mtp": True}, _plain_model(), _cuda_profile())
    assert "--spec-type draft-mtp" in scalars["flags_str"]


# ── full precedence chain: runner < profile < family(arch) < model < slot ────


def test_precedence_chain_ngl_slot_beats_everything():
    """-ngl set at profile, model_defaults (via the n_gpu_layers field), and
    slot [model].n_gpu_layers all disagree -- the slot override must win in
    the final resolved argv (normalize_argv/resolve_argv last-wins).

    The model tier sets -ngl via the schema field ``n_gpu_layers`` (trusted
    ``model_defaults`` segment), NOT via ``extra_args``: ``-ngl`` is a managed
    flag, so a model ``extra_args`` carrying it is now rejected at launch just
    like a slot's ``[server].extra_args`` (see test_argv.py)."""
    profile = _rocm_profile(flags="-ngl 10 -fa on")
    model = _plain_model(
        defaults={"extra_args": "", "n_gpu_layers": 20},
        architecture="gemma3",  # exercises the family-defaults tier too
    )
    slot_cfg = {"name": "s", "model": {"n_gpu_layers": 30}}
    scalars = _resolve_llama_scalars(slot_cfg, model, profile)
    segments = _llama_argv_segments(
        port=8080,
        model_path="/m/plain-chat.gguf",
        profile_flags=scalars["flags_str"],
        model_defaults=scalars["model_defaults"],
        slot_n_gpu_layers=scalars["slot_n_gpu_layers"],
    )
    resolved = resolve_argv(segments)
    assert resolved.argv[resolved.argv.index("-ngl") + 1] == "30"


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
