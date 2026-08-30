"""Tests for hal0.registry.runner_image_sync.sort_tags_newest_first.

The tag-tracking headline (runner-image-catalogue v2, spec
2026-08-24-runner-image-catalogue-v2-design.md) needs one deterministic
"newest first" ordering for GHCR ``tags/list`` output: date-shaped numeric
tags (``0824``) beat everything and sort descending, semver-shaped tags
sort descending after them, and anything else keeps registry order as the
stable last resort.
"""

from __future__ import annotations

from hal0.registry.runner_image_sync import is_noise_tag, sort_tags_newest_first


def test_date_shaped_numerics_beat_everything_and_sort_desc() -> None:
    assert sort_tags_newest_first(["0822", "latest", "0824", "v1"])[:2] == ["0824", "0822"]


def test_semver_sorts_desc_after_numerics() -> None:
    out = sort_tags_newest_first(["v1.2.0", "v1.10.0", "latest"])
    assert out[:2] == ["v1.10.0", "v1.2.0"]


def test_registry_order_is_last_resort_and_stable() -> None:
    assert sort_tags_newest_first(["alpha", "beta"]) == ["alpha", "beta"]


def test_empty_ok() -> None:
    assert sort_tags_newest_first([]) == []


def test_buckets_concatenate_numeric_then_semver_then_rest() -> None:
    out = sort_tags_newest_first(["latest", "v1.2.0", "0824", "alpha", "1.10.0", "0822"])
    assert out == ["0824", "0822", "1.10.0", "v1.2.0", "latest", "alpha"]


# ── is_noise_tag (cosign signature/attestation + per-commit CI tags) ────────


def test_cosign_signature_tag_is_noise() -> None:
    assert is_noise_tag("sha256-" + "a" * 64 + ".sig") is True


def test_cosign_attestation_tag_is_noise() -> None:
    assert is_noise_tag("sha256-" + "0123456789abcdef" * 4 + ".att") is True


def test_bare_cosign_digest_tag_is_noise() -> None:
    """cosign also pushes the bare ``sha256-<hex>`` digest tag with no suffix."""
    assert is_noise_tag("sha256-" + "f" * 64) is True


def test_ci_commit_short_sha_tag_is_noise() -> None:
    assert is_noise_tag("sha-abc1234") is True


def test_ci_commit_full_sha_tag_is_noise() -> None:
    assert is_noise_tag("sha-" + "a" * 40) is True


def test_real_tags_are_not_noise() -> None:
    for tag in ("latest", "v1.2.3", "0824", "main", "server", "vulkan", "sha256abc"):
        assert is_noise_tag(tag) is False


def test_cosign_regex_requires_full_64_hex_digest() -> None:
    """A too-short hex run isn't a cosign artifact — don't over-match."""
    assert is_noise_tag("sha256-" + "a" * 63) is False


def test_ci_commit_regex_rejects_uppercase_and_short_runs() -> None:
    assert is_noise_tag("sha-ABC1234") is False
    assert is_noise_tag("sha-abc12") is False
