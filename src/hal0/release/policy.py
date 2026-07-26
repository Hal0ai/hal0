"""Tag-based release-policy derivation — the single source of truth for
classifying git tags as stable, preview, or nightly releases, and for
producing GitHub Actions outputs and PyPI version strings.

Stdlib only (re, dataclasses, argparse, json, typing.Literal) so that
workflows can consume it via ``python3 -m hal0.release.policy`` with no
editable install.

Usage::

    uv run python -m hal0.release.policy v1.0.0-alpha.1
    uv run python -m hal0.release.policy v1.0.0 --format github
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

ReleaseKind = Literal["stable", "preview", "nightly"]
PreviewStage = Literal["alpha", "beta", "rc"]

_PREVIEW = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)-(?P<stage>alpha|beta|rc)\.(?P<seq>0|[1-9]\d*)$")
_FINAL = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)$")
_NIGHTLY = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)-nightly\.(?P<stamp>\d{14})$")


class ReleaseTagError(ValueError):
    """Raised when a tag does not match any supported release pattern."""

    pass


@dataclass(frozen=True)
class ReleasePolicy:
    """Immutable policy derived from a parsed release tag."""

    tag: str
    base_version: str
    version: str
    python_version: str | None
    kind: ReleaseKind
    prerelease_stage: PreviewStage | None
    manifest_targets: tuple[str, ...]
    github_prerelease: bool
    github_latest: bool
    publish_pypi: bool
    retain: bool

    @classmethod
    def from_tag(cls, tag: str) -> ReleasePolicy:
        """Parse *tag* and return a :class:`ReleasePolicy` for it.

        Raises :class:`ReleaseTagError` if the tag does not match any
        supported pattern.
        """
        if match := _PREVIEW.fullmatch(tag):
            stage = match.group("stage")
            seq = match.group("seq")
            marker = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
            return cls(
                tag=tag,
                base_version=match.group("base"),
                version=tag[1:],
                python_version=f"{match.group('base')}{marker}{seq}",
                kind="preview",
                prerelease_stage=stage,  # type: ignore[arg-type]
                manifest_targets=("preview",),
                github_prerelease=True,
                github_latest=False,
                publish_pypi=True,
                retain=True,
            )
        if match := _FINAL.fullmatch(tag):
            version = match.group("base")
            return cls(
                tag=tag,
                base_version=version,
                version=version,
                python_version=version,
                kind="stable",
                prerelease_stage=None,
                manifest_targets=("stable", "preview"),
                github_prerelease=False,
                github_latest=True,
                publish_pypi=True,
                retain=True,
            )
        if match := _NIGHTLY.fullmatch(tag):
            return cls(
                tag=tag,
                base_version=match.group("base"),
                version=tag[1:],
                python_version=None,
                kind="nightly",
                prerelease_stage=None,
                manifest_targets=("nightly",),
                github_prerelease=True,
                github_latest=False,
                publish_pypi=False,
                retain=False,
            )
        raise ReleaseTagError(f"unsupported release tag: {tag!r}")

    def to_github_outputs(self) -> dict[str, str]:
        """Return a ``key=value``-compatible dict for GitHub Actions outputs.

        All boolean fields are rendered as lowercase strings (``"true"`` /
        ``"false"``).  ``None`` fields are rendered as empty strings.
        """
        return {
            "tag": self.tag,
            "base_version": self.base_version,
            "version": self.version,
            "python_version": self.python_version or "",
            "kind": self.kind,
            "prerelease_stage": self.prerelease_stage or "",
            "manifest_targets": ",".join(self.manifest_targets),
            "github_prerelease": str(self.github_prerelease).lower(),
            "github_latest": str(self.github_latest).lower(),
            "publish_pypi": str(self.publish_pypi).lower(),
            "retain": str(self.retain).lower(),
        }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Derive release policy from a git tag.")
    parser.add_argument("tag", help="Git tag (e.g. v1.0.0-alpha.1)")
    parser.add_argument(
        "--format",
        choices=["json", "github"],
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args()
    policy = ReleasePolicy.from_tag(args.tag)
    if args.format == "github":
        for key, value in policy.to_github_outputs().items():
            print(f"{key}={value}")
    else:
        print(json.dumps(asdict(policy)))


if __name__ == "__main__":
    _main()
