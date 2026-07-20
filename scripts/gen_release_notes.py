#!/usr/bin/env python3
"""Generate ``RELEASE_NOTES.md`` + ``release.json`` into a release tarball root.

The release workflow stages these two files at the tarball root
(``hal0-<version>/``) so the updater's ``prepare()`` step can show
cosign-verified release notes before ``commit``
(``src/hal0/updater/updater.py::_read_release_notes``). ``RELEASE_NOTES.md`` is
the human-facing markdown; ``release.json`` carries the structured
``{highlights, breaking, migrations}`` the ``hal0 update`` CLI renders as
callouts. Because they live inside the cosign-verified tarball (not a plain-TLS
URL), what an operator reviews is exactly what was signed.

Source of truth: the matching ``## [<version>]`` section of CHANGELOG.md (Keep a
Changelog). Its ``### Highlights`` / ``### Breaking`` / ``### Migrations``
subsections populate ``release.json``. For the ``nightly`` channel (which has no
changelog section) the markdown is a commit log since the previous nightly tag
and the structured lists are empty. If nothing resolves, notes degrade to a
one-liner — the updater treats notes as optional, so an un-noted release still
installs cleanly.

Run from the repo root (needs full git history for the nightly / fallback ranges,
i.e. an ``actions/checkout`` with ``fetch-depth: 0``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Import the pure helpers without an editable install (mirrors the workflow's
# ``PYTHONPATH=src python3 -c …`` convention).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hal0.release.notes import extract_changelog_section, extract_structured


def _git(*args: str) -> str:
    """Run a git command, returning trimmed stdout or ``""`` on any failure."""
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return ""


def _prev_tag(tag: str, *, nightly: bool) -> str:
    """Most recent release tag before *tag* (nightly picks the prior nightly)."""
    tags = [t for t in _git("tag", "--list", "v*", "--sort=-creatordate").splitlines() if t != tag]
    if nightly:
        nightlies = [t for t in tags if "-nightly." in t]
        stables = [t for t in tags if "-nightly." not in t]
        return (nightlies or stables or [""])[0]
    stables = [t for t in tags if "-nightly." not in t]
    return (stables or [""])[0]


def _git_changelog(tag: str, *, nightly: bool) -> str:
    """Fallback markdown: a commit log since the previous relevant tag."""
    prev = _prev_tag(tag, nightly=nightly)
    rng = f"{prev}..HEAD" if prev else "HEAD"
    log = _git("log", "--no-merges", "--pretty=- %s", rng) or "- (no changelog available)"
    label = f"since {prev}" if prev else "since the initial commit"
    return f"# hal0 {tag.lstrip('v')}\n\nChanges {label}:\n\n{log}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate RELEASE_NOTES.md + release.json")
    ap.add_argument("--tag", required=True, help="release tag, e.g. v0.10.0")
    ap.add_argument("--channel", default="stable", choices=["stable", "nightly", "preview"])
    ap.add_argument("--out-dir", required=True, help="tarball root dir to write into")
    ap.add_argument("--changelog", default="CHANGELOG.md")
    args = ap.parse_args()

    version = args.tag.lstrip("v")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    markdown = ""
    structured: dict[str, list[str]] = {"highlights": [], "breaking": [], "migrations": []}
    source = ""

    if args.channel != "nightly":
        changelog_path = Path(args.changelog)
        section = ""
        if changelog_path.is_file():
            section = extract_changelog_section(
                changelog_path.read_text(encoding="utf-8"), args.tag
            )
        if section:
            markdown = f"# hal0 {version}\n\n{section}\n"
            structured = extract_structured(section)
            source = f"{args.changelog}#{version}"

    if not markdown:
        markdown = _git_changelog(args.tag, nightly=(args.channel == "nightly"))
        source = "git-log"

    # Preview notes: add required headings when absent.
    if args.channel == "preview":
        required_headings = [
            "Audience",
            "Known issues",
            "Supported upgrades",
            "Operator migrations",
            "Rollback",
        ]
        for heading in required_headings:
            if f"## {heading}" not in markdown:
                markdown += f"\n## {heading}\n\nTBD\n"

    (out / "RELEASE_NOTES.md").write_text(markdown, encoding="utf-8")
    release = {"version": version, "channel": args.channel, "source": source, **structured}
    (out / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")

    print(
        f"release-notes → {out}: RELEASE_NOTES.md ({len(markdown)}B) + release.json "
        f"(highlights={len(structured['highlights'])} breaking={len(structured['breaking'])} "
        f"migrations={len(structured['migrations'])}, source={source})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
