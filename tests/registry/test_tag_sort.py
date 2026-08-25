"""Tests for hal0.registry.runner_image_sync.sort_tags_newest_first.

The tag-tracking headline (runner-image-catalogue v2, spec
2026-08-24-runner-image-catalogue-v2-design.md) needs one deterministic
"newest first" ordering for GHCR ``tags/list`` output: date-shaped numeric
tags (``0824``) beat everything and sort descending, semver-shaped tags
sort descending after them, and anything else keeps registry order as the
stable last resort.
"""

from __future__ import annotations

from hal0.registry.runner_image_sync import sort_tags_newest_first


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
