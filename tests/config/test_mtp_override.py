"""Tests for per-slot MTP override in resolve_profile_flags and container threading.

§7.1a / ML-5: MTP moved from a profile-template property to a MODEL
capability. Covers:
  - resolve_profile_flags(profile, mtp_override=True/False/None) — profile.mtp
    is NO LONGER consulted; the caller must pass an explicit bool to expand
    the bundle, ``None`` behaves like ``False``.
  - _profile_image_and_flags(profile, mtp_override) threading (unchanged
    mechanically).
  - build_mtp_flag_bundle(backend) draft-device derivation (unchanged).
  - model_is_mtp_eligible(model_info): explicit ``defaults.mtp`` tri-state
    wins in EITHER direction over the registry ``mtp`` tag; the old
    filename/GGUF-name ``MTP`` marker sniff is REMOVED.
  - _effective_mtp(slot_mtp, model_info, runner) precedence: slot.mtp ->
    model.defaults.mtp -> (registry mtp tag AND runner.supports.mtp).
"""

from hal0.config.schema import (
    MTP_FLAG_BUNDLE,
    ProfileConfig,
    build_mtp_flag_bundle,
    resolve_profile_flags,
)
from hal0.model_meta import model_is_mtp_eligible
from hal0.providers.container import _effective_mtp, _profile_image_and_flags
from hal0.runners import RUNNER_IMAGES, get_runner


def _profile(mtp: bool = False) -> ProfileConfig:
    # mtp is kept on the fixture only to prove it's now INERT for flag
    # resolution — resolve_profile_flags no longer reads it.
    return ProfileConfig(
        flags="-fa on -b 512",
        mtp=mtp,
        device_class="gpu",
        backend="rocm",
    )


# ── resolve_profile_flags: profile.mtp is inert, only the explicit override counts ──


def test_override_true_appends_bundle_regardless_of_profile_mtp():
    out = resolve_profile_flags(_profile(False), mtp_override=True)
    assert MTP_FLAG_BUNDLE in out


def test_override_false_drops_bundle_regardless_of_profile_mtp():
    out = resolve_profile_flags(_profile(True), mtp_override=False)
    assert MTP_FLAG_BUNDLE not in out
    assert out == "-fa on -b 512"


def test_override_none_never_expands_even_when_profile_mtp_true():
    """The old 'inherit profile.mtp when override is None' behaviour is
    GONE — profile.mtp is informational only now."""
    assert MTP_FLAG_BUNDLE not in resolve_profile_flags(_profile(True), mtp_override=None)
    assert MTP_FLAG_BUNDLE not in resolve_profile_flags(_profile(False), mtp_override=None)
    assert MTP_FLAG_BUNDLE not in resolve_profile_flags(_profile(True))  # default arg too


# ── Container-provider threading ─────────────────────────────────────────────


def test_profile_image_and_flags_honors_override():
    p = ProfileConfig(flags="-fa on", mtp=False, device_class="gpu", backend="rocm")
    _, on = _profile_image_and_flags(p, True)
    assert MTP_FLAG_BUNDLE in on
    _, off = _profile_image_and_flags(p, None)
    assert MTP_FLAG_BUNDLE not in off


# ── build_mtp_flag_bundle: draft device tracks the profile backend ───────────


def test_bundle_draft_device_tracks_backend():
    assert "--spec-draft-device ROCm0" in build_mtp_flag_bundle("rocm")
    assert "--spec-draft-device Vulkan0" in build_mtp_flag_bundle("vulkan")
    assert "--spec-draft-device CUDA0" in build_mtp_flag_bundle("cuda")


def test_bundle_unknown_backend_defaults_rocm0():
    # Backend-less / non-GPU profiles keep the historical ROCm0 default so the
    # bundle is byte-identical to the pre-separation constant.
    assert "--spec-draft-device ROCm0" in build_mtp_flag_bundle(None)
    assert build_mtp_flag_bundle("rocm") == MTP_FLAG_BUNDLE


def test_vulkan_profile_drafts_on_vulkan_device():
    p = ProfileConfig(flags="-fa on", mtp=True, device_class="gpu", backend="vulkan")
    out = resolve_profile_flags(p, mtp_override=True)
    assert "--spec-draft-device Vulkan0" in out
    assert "--spec-draft-device ROCm0" not in out


# ── model_is_mtp_eligible: explicit defaults.mtp wins, else the registry tag ──


def test_eligible_by_registry_tag():
    assert model_is_mtp_eligible({"_model_key": "plain-name", "tags": ["chat", "mtp"]}) is True


def test_not_eligible_plain_model_no_tag_no_defaults():
    assert model_is_mtp_eligible({"_model_key": "gemma-4-12b-it", "tags": ["chat"]}) is False
    assert model_is_mtp_eligible({"_model_key": "temptation-7b"}) is False


def test_no_filename_marker_sniffing_anymore():
    """§7.1a / ML-5: the old _MTP_NAME_RE path is REMOVED — an 'MTP' token
    in the model id/GGUF name no longer makes a model eligible on its own."""
    assert (
        model_is_mtp_eligible({"_model_key": "CHADROCK3.6-35B-UNCENSORED-MTP-STRIX-LEAN"}) is False
    )
    assert model_is_mtp_eligible({"path": "/m/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"}) is False


def test_explicit_defaults_mtp_true_wins_over_absent_tag():
    assert (
        model_is_mtp_eligible({"_model_key": "plain-name", "tags": [], "defaults": {"mtp": True}})
        is True
    )


def test_explicit_defaults_mtp_false_wins_over_present_tag():
    """The explicit tri-state override wins in EITHER direction — even
    suppressing a model that DOES carry the registry tag."""
    assert (
        model_is_mtp_eligible({"_model_key": "tagged", "tags": ["mtp"], "defaults": {"mtp": False}})
        is False
    )


def test_defaults_mtp_none_falls_back_to_tag():
    assert (
        model_is_mtp_eligible({"_model_key": "tagged", "tags": ["mtp"], "defaults": {"mtp": None}})
        is True
    )
    assert (
        model_is_mtp_eligible({"_model_key": "untagged", "tags": [], "defaults": {"mtp": None}})
        is False
    )


# ── _effective_mtp: slot -> model.defaults.mtp -> (tag AND runner.supports.mtp) ──


_ROCMFPX = get_runner("rocmfpx")  # supports.mtp = True
_CUDA = get_runner("cuda")  # supports.mtp = False


def _mtp_model(**overrides):
    base = {"_model_key": "chadrock-35b-mtp", "tags": ["chat", "mtp"]}
    base.update(overrides)
    return base


def _plain_model(**overrides):
    base = {"_model_key": "gemma-4-12b-it", "tags": ["chat"]}
    base.update(overrides)
    return base


def test_auto_on_when_tag_eligible_and_runner_supports_mtp():
    assert _effective_mtp(None, _mtp_model(), _ROCMFPX) is True


def test_auto_off_when_model_not_eligible():
    # The dead-flags fix: a plain (untagged) model does NOT speculate.
    assert _effective_mtp(None, _plain_model(), _ROCMFPX) is False


def test_auto_off_when_runner_does_not_support_mtp():
    """NEW (§7.1a / ML-5): a tag-eligible model on a runner that can't draft
    (e.g. the cuda llama-server lane) stays off under auto — the old
    profile.mtp gate is replaced by this runner-capability gate."""
    assert _effective_mtp(None, _mtp_model(), _CUDA) is False


def test_slot_override_true_forces_on_even_for_plain_model_or_unsupported_runner():
    assert _effective_mtp(True, _plain_model(), _CUDA) is True


def test_slot_override_false_forces_off_even_for_eligible():
    assert _effective_mtp(False, _mtp_model(), _ROCMFPX) is False


def test_defaults_mtp_true_beats_absent_tag_and_is_unconditional():
    """defaults.mtp is an explicit, unconditional curator override — like
    slot.mtp, it wins even for an untagged model AND even on a runner that
    doesn't support MTP drafting (an operator/curator override, not a
    guess)."""
    model = _plain_model(defaults={"mtp": True})
    assert _effective_mtp(None, model, _CUDA) is True


def test_defaults_mtp_false_beats_present_tag():
    model = _mtp_model(defaults={"mtp": False})
    assert _effective_mtp(None, model, _ROCMFPX) is False


def test_defaults_mtp_none_falls_back_to_tag_and_runner_gate():
    model = _mtp_model(defaults={"mtp": None})
    assert _effective_mtp(None, model, _ROCMFPX) is True
    assert _effective_mtp(None, model, _CUDA) is False


def test_auto_off_breadcrumb_only_on_launch_path(caplog):
    """The auto-off log is launch-gated: _effective_mtp sits inside the SHARED
    launch/preview resolver, and the preview path runs on every dashboard
    GET /api/slots poll — an ungated log turned a once-per-launch hint into a
    ~0.4/s stream per polling client (measured 43 logs : 43 status GETs)."""
    import logging

    with caplog.at_level(logging.INFO, logger="hal0.providers.container"):
        # Preview/status path (default): silent.
        assert _effective_mtp(None, _plain_model(), _ROCMFPX) is False
        assert not [r for r in caplog.records if "auto_off" in r.getMessage()]
        # Launch path: exactly one breadcrumb.
        assert _effective_mtp(None, _plain_model(), _ROCMFPX, log_ineligible=True) is False
        assert len([r for r in caplog.records if "auto_off" in r.getMessage()]) == 1


def test_runner_capability_registry_agrees_with_test_fixtures():
    """Sanity pin: rocmfpx supports MTP, cuda doesn't — if the registry ever
    changes this, the tests above intentionally need a second look."""
    assert RUNNER_IMAGES["rocmfpx"].supports.mtp is True
    assert RUNNER_IMAGES["cuda"].supports.mtp is False
