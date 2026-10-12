"""ADR-0029: mtp/jinja as RUNNER-gated capabilities resolved by
``_resolve_llama_scalars``.

``--jinja`` is a model-layer fact: hal0 adds it when the launching runner
supports it, unless the model file's ``jinja`` is ``False`` or the profile
runs in embedding/reranking mode. MTP drafting is the profile's ``mtp``
(``auto``/``off``): ``auto`` drafts when the model has MTP heads (an explicit
``defaults.mtp`` fact, else the registry ``mtp`` tag) AND the runner supports
drafting. The runner is the slot's (``binary``, else derived from its
``device``).
"""

from __future__ import annotations

import pytest

from hal0.config.schema import FAMILY_DEFAULTS, ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import _resolve_llama_scalars, _segments_from_scalars
from hal0.slots.argv import resolve_argv

_ROCM_SLOT = {"name": "s", "device": "gpu-rocm"}
_CUDA_SLOT = {"name": "s", "device": "gpu-cuda"}


def _profile(**overrides) -> ProfileConfig:
    base = dict(flags="-fa on -b 512")
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


def _argv(slot_cfg: dict, model: dict, profile: ProfileConfig) -> list[str]:
    scalars = _resolve_llama_scalars(slot_cfg, model, profile)
    return resolve_argv(_segments_from_scalars(8080, str(model["path"]), scalars)).argv


# ── --jinja capability injection ──────────────────────────────────────────────


def test_jinja_default_on_for_llama_server_runner():
    scalars = _resolve_llama_scalars(_ROCM_SLOT, _plain_model(), _profile())
    assert scalars["jinja"] is True
    assert "--jinja" in _argv(_ROCM_SLOT, _plain_model(), _profile())


def test_jinja_suppressed_by_defaults_jinja_false():
    model = _plain_model(defaults={"jinja": False})
    assert _resolve_llama_scalars(_ROCM_SLOT, model, _profile())["jinja"] is False
    assert "--jinja" not in _argv(_ROCM_SLOT, model, _profile())


def test_jinja_defaults_true_and_none_both_mean_on():
    for jinja_val in (True, None):
        model = _plain_model(defaults={"jinja": jinja_val})
        assert _resolve_llama_scalars(_ROCM_SLOT, model, _profile())["jinja"] is True, jinja_val


@pytest.mark.parametrize("mode", ["--embedding", "--reranking"])
def test_jinja_never_injected_in_embedding_or_reranking_mode(mode: str):
    """embed/rerank never gets --jinja (llama-server's --jinja is a chat-
    completions feature, meaningless in --embedding mode). The mode marker is
    the profile's (the shipped ``embedding`` / ``reranking`` seeds)."""
    profile = _profile(flags=f"{mode} -fa auto -b 8192 -ub 8192")
    assert _resolve_llama_scalars(_ROCM_SLOT, _plain_model(), profile)["jinja"] is False
    argv = _argv(_ROCM_SLOT, _plain_model(), profile)
    assert "--jinja" not in argv
    assert mode in argv


def test_profile_flags_are_the_tune():
    """ADR-0029: the profile's flag text is what launches, verbatim."""
    scalars = _resolve_llama_scalars(_ROCM_SLOT, _plain_model(), _profile())
    assert scalars["profile_flags"] == "-fa on -b 512"


def test_profile_jinja_flag_refuses_to_launch():
    """``--jinja`` is a model fact hal0 adds; a profile carrying it is refused."""
    with pytest.raises(BadRequest) as excinfo:
        _argv(_ROCM_SLOT, _plain_model(), _profile(flags="-fa on --jinja"))
    assert excinfo.value.code == "profiles.model_fact_flag_denied"


# ── mtp: the profile decides, runner- and model-gated ─────────────────────────


def test_mtp_bundle_present_for_tagged_model_on_rocm():
    """profile ``mtp = "auto"`` drafts when the model is tagged ``mtp`` and the
    runner supports drafting; the draft device follows the slot's device."""
    argv = _argv(_ROCM_SLOT, _mtp_tagged_model(), _profile())
    assert "--spec-type" in argv
    assert argv[argv.index("--spec-type") + 1] == "draft-mtp"
    assert argv[argv.index("--spec-draft-device") + 1] == "ROCm0"


def test_profile_mtp_off_never_drafts():
    scalars = _resolve_llama_scalars(_ROCM_SLOT, _mtp_tagged_model(), _profile(mtp="off"))
    assert scalars["mtp"] is False
    assert scalars["draft_device"] is None


def test_mtp_bundle_absent_for_untagged_model():
    scalars = _resolve_llama_scalars(_ROCM_SLOT, _plain_model(), _profile())
    assert scalars["mtp"] is False
    assert "--spec-type" not in _argv(_ROCM_SLOT, _plain_model(), _profile())


def test_mtp_bundle_absent_on_cuda_runner_even_for_tagged_model():
    """The cuda llama-server runner doesn't support MTP drafting
    (RUNNER_IMAGES["cuda"].supports.mtp is False) — a tagged model on a
    gpu-cuda slot does NOT speculate under ``auto``."""
    assert "--spec-type" not in _argv(_CUDA_SLOT, _mtp_tagged_model(), _profile())


def test_slot_mtp_key_refuses_to_launch():
    """A slot ``mtp`` key is pre-conversion debris: the slot is unconverted
    and does not launch (the launch never silently consults or drops it)."""
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity) as excinfo:
        _resolve_llama_scalars({"name": "s", "mtp": True}, _plain_model(), _profile())
    assert excinfo.value.code == "slot.unconverted"


def test_defaults_mtp_true_is_the_heads_fact_but_the_runner_still_gates():
    """ADR-0029: the model's ``defaults.mtp = True`` means "the file has MTP
    heads" — it no longer forces drafting. It drafts on a runner that
    supports MTP and stops drafting on one that does not."""
    model = _plain_model(defaults={"mtp": True})
    assert _resolve_llama_scalars(_ROCM_SLOT, model, _profile())["mtp"] is True
    assert _resolve_llama_scalars(_CUDA_SLOT, model, _profile())["mtp"] is False


def test_defaults_mtp_false_overrides_the_tag():
    model = _mtp_tagged_model(defaults={"mtp": False})
    assert _resolve_llama_scalars(_ROCM_SLOT, model, _profile())["mtp"] is False


# ── slot owns -ngl ────────────────────────────────────────────────────────────


def test_slot_ngl_is_the_ngl_and_model_ngl_key_is_ignored():
    """spec-hw-slot-ownership §2: the SLOT's top-level ``n_gpu_layers`` owns
    ``-ngl`` (the trusted ``slot`` layer). A leftover ``defaults.n_gpu_layers``
    key on a model_info dict emits nothing."""
    model = _plain_model(defaults={"n_gpu_layers": 20}, architecture="gemma3")
    slot_cfg = {**_ROCM_SLOT, "n_gpu_layers": 30}
    argv = _argv(slot_cfg, model, _profile())
    assert argv.count("-ngl") == 1
    assert argv[argv.index("-ngl") + 1] == "30"


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
