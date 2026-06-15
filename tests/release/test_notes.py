"""Unit tests for hal0.release.notes — the CHANGELOG section extractor."""

from __future__ import annotations

from hal0.release.notes import extract_changelog_section

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
