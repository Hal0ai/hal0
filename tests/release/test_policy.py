"""Unit tests for hal0.release.policy — tag-based release policy derivation.

See task-1-brief.md for the exact parametrize matrix and expected values.
"""

from __future__ import annotations

import pytest

from hal0.release.policy import ReleasePolicy, ReleaseTagError


@pytest.mark.parametrize(
    ("tag", "kind", "stage", "targets", "prerelease", "latest", "pypi", "python_version"),
    [
        ("v1.0.0-alpha.0", "preview", "alpha", ("preview",), True, False, True, "1.0.0a0"),
        ("v1.0.0-beta.2", "preview", "beta", ("preview",), True, False, True, "1.0.0b2"),
        ("v1.0.0-rc.1", "preview", "rc", ("preview",), True, False, True, "1.0.0rc1"),
        ("v1.0.0", "stable", None, ("stable", "preview"), False, True, True, "1.0.0"),
        (
            "v1.0.1-nightly.20260721060000",
            "nightly",
            None,
            ("nightly",),
            True,
            False,
            False,
            None,
        ),
    ],
)
def test_policy_matrix(
    tag: str,
    kind: str,
    stage: str | None,
    targets: tuple[str, ...],
    prerelease: bool,
    latest: bool,
    pypi: bool,
    python_version: str | None,
) -> None:
    policy = ReleasePolicy.from_tag(tag)
    assert policy.kind == kind
    assert policy.prerelease_stage == stage
    assert policy.manifest_targets == targets
    assert policy.github_prerelease is prerelease
    assert policy.github_latest is latest
    assert policy.publish_pypi is pypi
    assert policy.python_version == python_version


@pytest.mark.parametrize(
    "tag",
    [
        "1.0.0-alpha.1",
        "v1.0.0-alpha1",
        "v1.0.0-rc1",
        "v1.0.0-preview.1",
        "v1.0",
        "v1.0.0-alpha.-1",
        "v1.0.0-nightly.20260721",
    ],
)
def test_invalid_tags_fail_closed(tag: str) -> None:
    with pytest.raises(ReleaseTagError):
        ReleasePolicy.from_tag(tag)


def test_github_outputs_are_strings() -> None:
    outputs = ReleasePolicy.from_tag("v1.0.0-alpha.1").to_github_outputs()
    assert outputs == {
        "tag": "v1.0.0-alpha.1",
        "version": "1.0.0-alpha.1",
        "python_version": "1.0.0a1",
        "base_version": "1.0.0",
        "kind": "preview",
        "prerelease_stage": "alpha",
        "manifest_targets": "preview",
        "github_prerelease": "true",
        "github_latest": "false",
        "publish_pypi": "true",
        "retain": "true",
    }
