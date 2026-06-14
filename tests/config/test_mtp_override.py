"""Tests for per-slot MTP override in resolve_profile_flags and container threading.

Task 1 / Phase 2: slot-level mtp field can force MTP on or off independently of
the profile setting.  Covers:
  - resolve_profile_flags(profile, mtp_override=True/False/None)
  - _profile_image_and_flags(profile, mtp_override) threading
"""

from hal0.config.schema import MTP_FLAG_BUNDLE, ProfileConfig, resolve_profile_flags
from hal0.providers.container import _profile_image_and_flags


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
