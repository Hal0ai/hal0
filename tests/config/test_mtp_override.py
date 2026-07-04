"""Tests for per-slot MTP override in resolve_profile_flags and container threading.

Task 1 / Phase 2: slot-level mtp field can force MTP on or off independently of
the profile setting.  Covers:
  - resolve_profile_flags(profile, mtp_override=True/False/None)
  - _profile_image_and_flags(profile, mtp_override) threading

MTP separation: the auto decision now also factors *model* eligibility and the
draft device tracks the profile *backend*.  Covers:
  - build_mtp_flag_bundle(backend) draft-device derivation
  - model_is_mtp_eligible(model_info) (registry tag / name marker)
  - _effective_mtp(slot_mtp, profile, model_info) three-way resolution
"""

from hal0.config.schema import (
    MTP_FLAG_BUNDLE,
    ProfileConfig,
    build_mtp_flag_bundle,
    resolve_profile_flags,
)
from hal0.model_meta import model_is_mtp_eligible
from hal0.providers.container import _effective_mtp, _profile_image_and_flags


def _profile(mtp: bool) -> ProfileConfig:
    return ProfileConfig(
        image="img",
        flags="-fa on -b 512",
        mtp=mtp,
        device_class="gpu",
        backend="rocm",
    )


def test_override_true_appends_bundle_over_profile_false():
    out = resolve_profile_flags(_profile(False), mtp_override=True)
    assert MTP_FLAG_BUNDLE in out


def test_override_false_drops_bundle_over_profile_true():
    out = resolve_profile_flags(_profile(True), mtp_override=False)
    assert MTP_FLAG_BUNDLE not in out
    assert out == "-fa on -b 512"


def test_override_none_falls_back_to_profile():
    assert MTP_FLAG_BUNDLE in resolve_profile_flags(_profile(True), mtp_override=None)
    assert MTP_FLAG_BUNDLE not in resolve_profile_flags(_profile(False), mtp_override=None)


# ── Container-provider threading ─────────────────────────────────────────────


def test_profile_image_and_flags_honors_override():
    p = ProfileConfig(image="img", flags="-fa on", mtp=False, device_class="gpu", backend="rocm")
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
    p = ProfileConfig(image="img", flags="-fa on", mtp=True, device_class="gpu", backend="vulkan")
    out = resolve_profile_flags(p)
    assert "--spec-draft-device Vulkan0" in out
    assert "--spec-draft-device ROCm0" not in out


# ── model_is_mtp_eligible: registry tag or MTP name marker ───────────────────


def test_eligible_by_registry_tag():
    assert model_is_mtp_eligible({"_model_key": "plain-name", "tags": ["chat", "mtp"]}) is True


def test_eligible_by_name_marker():
    # Uncurated local pull with no tags — the MTP marker in the name gates it.
    assert (
        model_is_mtp_eligible({"_model_key": "CHADROCK3.6-35B-UNCENSORED-MTP-STRIX-LEAN"}) is True
    )
    assert model_is_mtp_eligible({"path": "/m/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"}) is True


def test_not_eligible_plain_model():
    assert model_is_mtp_eligible({"_model_key": "gemma-4-12b-it", "tags": ["chat"]}) is False
    # 'mtp' must be a delimited marker, not an incidental substring.
    assert model_is_mtp_eligible({"_model_key": "temptation-7b"}) is False


# ── _effective_mtp: slot override > (profile opt-in AND model eligibility) ────


def _mtp_model():
    return {"_model_key": "chadrock-35b-mtp", "tags": ["chat", "mtp"]}


def _plain_model():
    return {"_model_key": "gemma-4-12b-it", "tags": ["chat"]}


def test_auto_on_when_profile_opts_in_and_model_eligible():
    assert _effective_mtp(None, _profile(True), _mtp_model()) is True


def test_auto_off_when_model_not_eligible():
    # The dead-flags fix: a plain model on an MTP profile does NOT speculate.
    assert _effective_mtp(None, _profile(True), _plain_model()) is False


def test_auto_off_when_profile_does_not_opt_in():
    # An MTP model on a non-MTP profile stays off under auto (no silent enable).
    assert _effective_mtp(None, _profile(False), _mtp_model()) is False


def test_slot_override_true_forces_on_even_for_plain_model():
    assert _effective_mtp(True, _profile(False), _plain_model()) is True


def test_slot_override_false_forces_off_even_for_eligible():
    assert _effective_mtp(False, _profile(True), _mtp_model()) is False
