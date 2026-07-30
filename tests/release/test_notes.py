"""Unit tests for hal0.release.notes — the CHANGELOG section extractor."""

from __future__ import annotations

from hal0.release.notes import extract_changelog_section, extract_structured

# ── Sample CHANGELOG document used across tests ────────────────────────────

_CHANGELOG = """\
# Changelog

All notable changes to hal0 are recorded here.

## [v0.5.1-alpha.1] — 2026-06-15

Pre-Alpha. Added hal0 setup TUI.

### Added
- hal0 setup TUI replaces the web FirstRun picker.
- Ubuntu 26.04 / Python 3.14 install support.

### Removed
- Web FirstRun picker.

## [v0.5.0-alpha.1] — 2026-06-14

Pre-Alpha. Zero-boot install + FirstRun v2.

### Added
- FirstRun v2 wizard.
- Zero-boot installer.

## [v0.4.1-alpha.1] — 2026-06-14

Only one line here.
"""

_CHANGELOG_LAST_ONLY = """\
# Changelog

## [v0.3.2-alpha.1] — 2026-05-29

End-of-stream cut for v0.3. Bundles MCP-completion.
"""

_CHANGELOG_PREFIX_COLLISION = """\
# Changelog

## [v0.5.10-alpha.1] — 2026-07-01

This is v0.5.10, NOT v0.5.1.

## [v0.5.1-alpha.1] — 2026-06-15

This is v0.5.1.
"""


# ── Basic extraction ────────────────────────────────────────────────────────


def test_extracts_first_section():
    body = extract_changelog_section(_CHANGELOG, "v0.5.1-alpha.1")
    assert "Pre-Alpha. Added hal0 setup TUI." in body
    assert "hal0 setup TUI replaces the web FirstRun picker." in body
    # Must not bleed into the next section
    assert "Zero-boot install" not in body
    assert "## [v0.5.0" not in body


def test_extracts_middle_section():
    body = extract_changelog_section(_CHANGELOG, "v0.5.0-alpha.1")
    assert "Zero-boot install" in body
    assert "FirstRun v2 wizard." in body
    # Must not bleed into prior or next section
    assert "hal0 setup TUI" not in body
    assert "Only one line" not in body


def test_extracts_last_section_no_trailing_header():
    """Section at end of document (no following ## header) must be captured."""
    body = extract_changelog_section(_CHANGELOG, "v0.4.1-alpha.1")
    assert "Only one line here." in body


def test_last_section_in_single_entry_document():
    """Document with only one ## section (no trailing ##) is captured."""
    body = extract_changelog_section(_CHANGELOG_LAST_ONLY, "v0.3.2-alpha.1")
    assert "End-of-stream cut for v0.3." in body


# ── v-prefix handling ───────────────────────────────────────────────────────


def test_accepts_version_with_leading_v():
    body = extract_changelog_section(_CHANGELOG, "v0.5.1-alpha.1")
    assert body != ""


def test_accepts_version_without_leading_v():
    body = extract_changelog_section(_CHANGELOG, "0.5.1-alpha.1")
    # Must find the section even without the leading v
    assert "Pre-Alpha. Added hal0 setup TUI." in body


def test_with_and_without_v_return_same_result():
    with_v = extract_changelog_section(_CHANGELOG, "v0.5.1-alpha.1")
    without_v = extract_changelog_section(_CHANGELOG, "0.5.1-alpha.1")
    assert with_v == without_v


# ── Missing version ─────────────────────────────────────────────────────────


def test_missing_version_returns_empty_string():
    body = extract_changelog_section(_CHANGELOG, "v9.9.9")
    assert body == ""


def test_empty_changelog_returns_empty_string():
    assert extract_changelog_section("", "v0.5.1-alpha.1") == ""


def test_empty_version_returns_empty_string():
    assert extract_changelog_section(_CHANGELOG, "") == ""


# ── Prefix-collision guard ──────────────────────────────────────────────────


def test_does_not_match_version_that_is_prefix_of_another():
    """v0.5.1 must NOT match ## [v0.5.10-alpha.1]."""
    body = extract_changelog_section(_CHANGELOG_PREFIX_COLLISION, "v0.5.1-alpha.1")
    assert "This is v0.5.1." in body
    assert "This is v0.5.10" not in body


def test_longer_version_not_matched_by_shorter_query():
    """Querying v0.5.10 must NOT match ## [v0.5.1-alpha.1]."""
    body = extract_changelog_section(_CHANGELOG_PREFIX_COLLISION, "v0.5.10-alpha.1")
    assert "This is v0.5.10" in body
    assert "This is v0.5.1." not in body


# ── Return value properties ─────────────────────────────────────────────────


def test_result_is_stripped():
    """No leading/trailing whitespace in the returned body."""
    body = extract_changelog_section(_CHANGELOG, "v0.5.1-alpha.1")
    assert body == body.strip()


def test_header_line_excluded():
    """The ## [vX.Y.Z] header line itself is not in the output."""
    body = extract_changelog_section(_CHANGELOG, "v0.5.1-alpha.1")
    assert "## [v0.5.1-alpha.1]" not in body


# ── Structured extraction (release.json callouts) ────────────────────────────

_CHANGELOG_STRUCTURED = """\
## [v0.10.0] — 2026-07-05

Seed-profile cleanup + updater notes.

### Highlights
- 12 seed profiles; simpler rocm/vulkan flags
- release notes shown before commit

### Breaking
- removed rocm-moe / rocm-dnse
- renamed rocmfpx-moe -> vkfpx-moe

### Migrations
- slots on removed profiles auto-fall-back to rocm/vulkan

### Added
- something unrelated
"""


def test_extract_structured_pulls_all_three_lists():
    section = extract_changelog_section(_CHANGELOG_STRUCTURED, "v0.10.0")
    s = extract_structured(section)
    assert s["highlights"] == [
        "12 seed profiles; simpler rocm/vulkan flags",
        "release notes shown before commit",
    ]
    assert s["breaking"] == ["removed rocm-moe / rocm-dnse", "renamed rocmfpx-moe -> vkfpx-moe"]
    assert s["migrations"] == ["slots on removed profiles auto-fall-back to rocm/vulkan"]


def test_extract_structured_missing_subsections_are_empty():
    """A section with only Keep-a-Changelog ### Added yields empty callout lists."""
    section = extract_changelog_section(_CHANGELOG, "v0.5.0-alpha.1")
    assert extract_structured(section) == {"highlights": [], "breaking": [], "migrations": []}


def test_extract_structured_empty_input():
    assert extract_structured("") == {"highlights": [], "breaking": [], "migrations": []}


def test_extract_structured_case_insensitive_and_skips_nested_bullets():
    md = "### BREAKING\n- top level\n  - nested is skipped\n- another top\n"
    assert extract_structured(md)["breaking"] == ["top level", "another top"]


def test_extract_structured_accumulates_repeated_headings():
    """A repeated heading must not discard the earlier block (#1499).

    Union-resolving a CHANGELOG merge conflict routinely leaves two ``###
    Breaking`` blocks in one version section. Assigning per heading silently
    kept only the last, so an operator's ``hal0 update`` banner under-reported
    what the update actually does — a quieter safety callout, not a cosmetic
    bug. Order follows the document.
    """
    md = (
        "### Breaking\n- first breaking\n\n"
        "### Fixed\n- unrelated\n\n"
        "### Breaking\n- second breaking\n\n"
        "### Migrations\n- first migration\n\n"
        "### Migrations\n- second migration\n"
    )
    s = extract_structured(md)
    assert s["breaking"] == ["first breaking", "second breaking"]
    assert s["migrations"] == ["first migration", "second migration"]


# ── gen_release_notes.py script (produces the two tarball-root files) ─────────


def test_gen_release_notes_script_writes_both_files(tmp_path):
    import json as _json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "gen_release_notes.py"
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_CHANGELOG_STRUCTURED, encoding="utf-8")
    out = tmp_path / "stage"

    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--tag",
            "v0.10.0",
            "--out-dir",
            str(out),
            "--changelog",
            str(changelog),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "Seed-profile cleanup" in (out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    data = _json.loads((out / "release.json").read_text(encoding="utf-8"))
    assert data["version"] == "0.10.0"
    assert data["breaking"] == ["removed rocm-moe / rocm-dnse", "renamed rocmfpx-moe -> vkfpx-moe"]
    assert data["migrations"] == ["slots on removed profiles auto-fall-back to rocm/vulkan"]


# ── Preview channel notes ───────────────────────────────────────────────────


_PREVIEW_CHANGELOG = """\
# Changelog

## [v0.11.0-preview.1] — 2026-08-01

Preview cut for release validation.

### Highlights
- preview feature flag system

### Breaking
- removed legacy foo
"""


def test_preview_notes_include_required_headings(tmp_path):
    """Preview release notes include Audience, Known issues, Supported upgrades,
    Operator migrations, and Rollback headings."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "gen_release_notes.py"
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_PREVIEW_CHANGELOG, encoding="utf-8")
    out = tmp_path / "stage"

    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--tag",
            "v0.11.0-preview.1",
            "--channel",
            "preview",
            "--out-dir",
            str(out),
            "--changelog",
            str(changelog),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    notes = (out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    for heading in (
        "Audience",
        "Known issues",
        "Supported upgrades",
        "Operator migrations",
        "Rollback",
    ):
        assert f"## {heading}" in notes, f"missing heading: {heading}"
    import json as _json

    data = _json.loads((out / "release.json").read_text(encoding="utf-8"))
    assert data["channel"] == "preview"


def test_preview_notes_heading_audience_present(tmp_path):
    """Preview notes must contain ## Audience (required)."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "gen_release_notes.py"
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_PREVIEW_CHANGELOG, encoding="utf-8")
    out = tmp_path / "stage"

    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--tag",
            "v0.11.0-preview.1",
            "--channel",
            "preview",
            "--out-dir",
            str(out),
            "--changelog",
            str(changelog),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    notes = (out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "## Audience" in notes
    assert "## Known issues" in notes
