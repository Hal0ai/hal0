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
        ("v0.5.0-nightly.20260614", "nightly"),
        ("0.5.0-nightly.20260614", "nightly"),
        ("v0.5.0", "stable"),
        ("v0.5.0-alpha.1", "stable"),
        ("v1.0.0-rc1", "stable"),
        ("", "stable"),
    ],
)
def test_channel_for_tag(tag, expected):
    assert channel_for_tag(tag) == expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v0.5.0-nightly.20260614", "0.5.0"),
        ("0.5.0-alpha.1", "0.5.0"),
        ("0.5.0", "0.5.0"),
        ("v1.2.3", "1.2.3"),
    ],
)
def test_base_version(version, expected):
    assert base_version(version) == expected


def test_nightly_version_and_tag():
    assert nightly_version("0.5.0", "20260614") == "0.5.0-nightly.20260614"
    assert nightly_tag("0.5.0", "20260614") == "v0.5.0-nightly.20260614"


def test_base_matches_relaxed_gate():
    assert base_matches("0.5.0-alpha.1", "v0.5.0-nightly.20260614") is True
    assert base_matches("0.5.0", "v0.5.0-nightly.20260614") is True
    assert base_matches("0.6.0-alpha.1", "v0.5.0-nightly.20260614") is False


def test_nightlies_to_prune_keeps_newest():
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


def test_nightlies_to_prune_nothing_when_under_keep():
    tags = ["v0.5.0-nightly.20260613", "v0.5.0-nightly.20260614"]
    assert nightlies_to_prune(tags, keep=7) == []
