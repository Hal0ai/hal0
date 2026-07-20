"""Unit tests for hal0.release.channel — the version/channel helpers shared
by the release + nightly GitHub Actions workflows."""

from __future__ import annotations

import pytest

from hal0.release.channel import (
    base_matches,
    base_version,
    channel_for_tag,
    nightlies_to_prune,
    nightly_tag,
    nightly_version,
)


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.5.0-nightly.20260614000000", "nightly"),
        ("v0.5.0", "stable"),
        ("v0.5.0-alpha.1", "preview"),
        ("v0.5.0-beta.2", "preview"),
        ("v0.5.0-rc.1", "preview"),
    ],
)
def test_channel_for_tag(tag: str, expected: str) -> None:
    assert channel_for_tag(tag) == expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v0.5.0-nightly.20260614000000", "0.5.0"),
        ("0.5.0-alpha.1", "0.5.0"),
        ("0.5.0", "0.5.0"),
        ("v1.2.3", "1.2.3"),
    ],
)
def test_base_version(version: str, expected: str) -> None:
    assert base_version(version) == expected


def test_nightly_version_and_tag() -> None:
    assert nightly_version("0.5.0", "20260614") == "0.5.0-nightly.20260614"
    assert nightly_tag("0.5.0", "20260614") == "v0.5.0-nightly.20260614"


def test_base_matches_relaxed_gate() -> None:
    assert base_matches("0.5.0-alpha.1", "v0.5.0-nightly.20260614000000")
    assert base_matches("0.5.0", "v0.5.0-nightly.20260614000000")
    assert not base_matches("0.6.0-alpha.1", "v0.5.0-nightly.20260614000000")


def test_nightlies_to_prune_keeps_newest() -> None:
    tags = [
        "v0.5.0-nightly.20260610",
        "v0.5.0-nightly.20260611",
        "v0.5.0-nightly.20260612",
        "v0.5.0-nightly.20260613",
        "v0.5.0",
        "v0.5.0-alpha.1",
    ]
    assert sorted(nightlies_to_prune(tags, keep=2)) == [
        "v0.5.0-nightly.20260610",
        "v0.5.0-nightly.20260611",
    ]


def test_nightlies_to_prune_nothing_when_under_keep() -> None:
    tags = ["v0.5.0-nightly.20260613", "v0.5.0-nightly.20260614"]
    assert nightlies_to_prune(tags, keep=7) == []


def test_nightly_version_uses_full_stamp_and_is_monotonic() -> None:
    # a sub-day timestamp stamp is interpolated verbatim
    assert nightly_version("0.5.1", "20260615120000") == "0.5.1-nightly.20260615120000"
    assert nightly_tag("0.5.1", "20260615120000") == "v0.5.1-nightly.20260615120000"


def test_nightlies_to_prune_orders_by_full_numeric_stamp() -> None:
    tags = [
        "v0.5.1-nightly.20260615120000",
        "v0.5.1-nightly.20260615",  # legacy date-only (older)
        "v0.5.1-nightly.20260615130000",  # newest
        "v0.5.1-alpha.1",  # not a nightly — never pruned
    ]
    pruned = nightlies_to_prune(tags, keep=1)
    assert "v0.5.1-alpha.1" not in pruned  # stable never pruned
    assert "v0.5.1-nightly.20260615130000" not in pruned  # newest kept
    assert "v0.5.1-nightly.20260615" in pruned  # oldest pruned
