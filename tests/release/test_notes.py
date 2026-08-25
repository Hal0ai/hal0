"""Unit tests for hal0.release.notes — the CHANGELOG section extractor."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_extract_structured_joins_wrapped_continuation_lines():
    """A markdown-wrapped bullet is one entry, not its first physical line (#1874).

    Every real migration bullet wraps across 4-6 lines, so keeping only the first
    line dropped every remediation command out of the ``hal0 update`` callout.
    ``highlights`` and ``breaking`` wrap the same way and join the same way.
    """
    md = (
        "### Migrations\n"
        "- **Profile-less slots (#1830):** a slot created under rc.5 keeps its\n"
        "  old profile. Run `hal0 slot edit <name> --profile embedding`, then\n"
        "  `hal0 slot restart`.\n"
        "- second migration\n"
        "\n"
        "### Highlights\n"
        "- a highlight that\n"
        "  wraps too\n"
    )
    s = extract_structured(md)
    assert s["migrations"] == [
        "**Profile-less slots (#1830):** a slot created under rc.5 keeps its old "
        "profile. Run `hal0 slot edit <name> --profile embedding`, then `hal0 slot restart`.",
        "second migration",
    ]
    assert s["highlights"] == ["a highlight that wraps too"]


def test_extract_structured_nested_bullet_ends_its_parent_entry():
    """A sub-bullet is skipped and its own wrapped lines do not glue onto the parent."""
    md = "### Breaking\n- top level\n  wraps\n  - nested is skipped\n    and its tail too\n- another top\n"
    assert extract_structured(md)["breaking"] == ["top level wraps", "another top"]


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


# ── Regression: the *shipped* CHANGELOG.md, not a fixture (#1874) ────────────
#
# ``extract_structured`` was only ever exercised against synthetic fixtures that
# used a ``### Migrations`` heading. Every real release section writes ``###
# Operator migrations``, which matched nothing — so ``release.json``'s
# ``migrations`` list, the callout ``hal0 update`` renders before an operator
# confirms, has been empty for every release ever cut. These tests read the real
# CHANGELOG.md that ships in the tarball, so a heading the project actually uses
# can never again be silently unrecognised.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_CHANGELOG = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _shipped_sections() -> list[tuple[str, str]]:
    """``(version, body)`` for every ``## [<version>]`` section of CHANGELOG.md."""
    parts = re.split(r"^## \[v?([^\]]+)\][^\n]*$", _SHIPPED_CHANGELOG, flags=re.MULTILINE)
    it = iter(parts[1:])
    return [(v.strip(), body) for v, body in zip(it, it, strict=False)]


def _block_under(body: str, heading: str) -> str:
    """The raw markdown under ``### <heading>``, up to the next ``##``/``###``.

    Deliberately does **no** bullet parsing: the production defect this file
    guards was in how bullets and their wrapped continuation lines are turned
    into entries, so any helper that re-derived that rule would simply agree
    with the bug (that is exactly how round 1 shipped a truncating extractor
    under a green test). Expectations below are derived from literal strings
    present in this raw block instead.
    """
    m = re.search(rf"^###\s+{re.escape(heading)}\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    rest = body[m.end() :]
    stop = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return rest[: stop.start()] if stop else rest


def _flat(text: str) -> str:
    """Whitespace-collapsed text, so a markdown-wrapped phrase matches its joined form."""
    return " ".join(text.split())


_SECTIONS_WITH_OPERATOR_MIGRATIONS = [
    pytest.param(version, body, id=version)
    for version, body in _shipped_sections()
    if re.search(r"^[-*] ", _block_under(body, "Operator migrations"), re.MULTILINE)
]


def test_shipped_changelog_actually_uses_the_operator_migrations_heading():
    """Guard the guard: if the convention ever changes, the sweep below must not
    quietly degrade into asserting nothing."""
    assert _SECTIONS_WITH_OPERATOR_MIGRATIONS, (
        "no shipped CHANGELOG section carries '### Operator migrations' with bullets"
    )


@pytest.mark.parametrize(("version", "body"), _SECTIONS_WITH_OPERATOR_MIGRATIONS)
def test_shipped_operator_migrations_reach_release_json(version, body):
    """Every real release section that documents operator migrations must yield
    them in ``release.json``'s ``migrations`` list — whole, not truncated.

    The completeness expectation is every inline-code span the block's raw
    markdown contains (commands, paths, config keys). Those are what an operator
    has to type, they are exactly what a first-physical-line-only extractor
    dropped, and they are read straight out of the CHANGELOG text rather than
    re-parsed with the rule under test.
    """
    block = _block_under(body, "Operator migrations")
    extracted = extract_structured(body)["migrations"]
    assert extracted, f"{version}: documents operator migrations, extracted none"

    joined = _flat(" ".join(extracted))
    for span in re.findall(r"`([^`]+)`", block):
        assert _flat(span) in joined, (
            f"{version}: `{_flat(span)}` documented under Operator migrations but missing "
            f"from the extracted callout — entry truncated at its first line? {extracted}"
        )


@pytest.mark.parametrize(("version", "body"), _SECTIONS_WITH_OPERATOR_MIGRATIONS)
def test_shipped_migration_entries_are_not_cut_mid_markdown(version, body):
    """A truncated entry leaves an unterminated ``**``/backtick span. rc.5's first
    migration ended mid-noun with a dangling bold marker in the rendered callout."""
    for entry in extract_structured(body)["migrations"]:
        assert entry.count("**") % 2 == 0, f"{version}: unterminated bold span: {entry!r}"
        assert entry.count("`") % 2 == 0, f"{version}: unterminated code span: {entry!r}"


def test_rc6_operator_migrations_carry_their_remediation_commands():
    """#1874's headline case: the rc.6 operator migrations (#1830 / #1827 / #1828)
    must reach the update callout *with the command the operator has to run*.

    ``hal0 slot edit`` sits past the first line break of the #1830 bullet, so the
    heading fix alone renders a sentence fragment and this assertion fails. The
    rc.6 CHANGELOG section is owned by a separate PR; until it lands this pins
    rc.5 (whose ``hal0 doctor ports --fix`` is likewise past the first break) and
    self-arms for rc.6 the moment that section exists.
    """
    expected_commands = {
        "1.0.0-rc.6": ("hal0 slot edit", "hal0 doctor profiles"),
        "1.0.0-rc.5": ("hal0 doctor ports --fix",),
    }
    sections = dict(_shipped_sections())
    version = "1.0.0-rc.6" if "1.0.0-rc.6" in sections else "1.0.0-rc.5"
    migrations = extract_structured(sections[version])["migrations"]
    assert migrations, f"{version} documents operator migrations but extracted none"

    joined = _flat(" ".join(migrations))
    for command in expected_commands[version]:
        assert command in joined, f"{version}: remediation command {command!r} lost: {migrations}"
    if version == "1.0.0-rc.6":
        for issue in ("#1830", "#1827", "#1828"):
            assert issue in joined, f"rc.6 migration for {issue} missing from {migrations}"
    else:
        assert "#1819" in joined, migrations


def test_heading_aliases_fold_onto_the_three_release_json_keys():
    """Variant spellings normalise instead of adding a fourth key — release.json
    keeps the shape ``hal0.updater.updater._read_release_notes`` expects."""
    s = extract_structured(
        "### Operator migrations\n- run doctor ports\n\n"
        "### Breaking changes\n- removed the old seam\n\n"
        "### Migrations\n- and the short spelling too\n"
    )
    assert sorted(s) == ["breaking", "highlights", "migrations"]
    assert s["migrations"] == ["run doctor ports", "and the short spelling too"]
    assert s["breaking"] == ["removed the old seam"]


def test_no_shipped_structured_heading_variant_is_unrecognised():
    """Sibling-risk sweep: any breaking/migration-flavoured ``###`` heading the real
    CHANGELOG uses must land in one of the structured keys. Catches a future
    ``### Breaking changes`` the same way this caught ``### Operator migrations``."""
    headings = {
        h.strip()
        for h in re.findall(r"^###\s+(.+?)\s*$", _SHIPPED_CHANGELOG, flags=re.MULTILINE)
        if re.search(r"migrat|breaking", h, re.IGNORECASE)
    }
    unrecognised = sorted(
        h for h in headings if not any(extract_structured(f"### {h}\n- probe\n").values())
    )
    assert not unrecognised, f"CHANGELOG headings that extract into nothing: {unrecognised}"
