"""Changelog / release-notes helpers shared by the release + nightly workflows.

Pure functions — no I/O, stdlib only — so both .github/workflows/release.yml
and .github/workflows/nightly.yml can call them via a bare
``PYTHONPATH=src python3 -c …`` (no editable install needed).
"""

from __future__ import annotations

import re


def extract_changelog_section(changelog: str, version: str) -> str:
    """Return the body of the ``## [<version>]`` section of a Keep-a-Changelog
    document (everything from that header up to the next ``## `` header,
    header line excluded), stripped.

    *version* is matched with or without a leading ``v``; e.g. both
    ``"v0.5.1-alpha.1"`` and ``"0.5.1-alpha.1"`` find the
    ``## [v0.5.1-alpha.1]`` section.

    Returns ``""`` if no matching section is found.

    Notes
    -----
    The regex uses a word-boundary-like anchor after the version string so
    that ``v0.5.1`` does **not** match ``## [v0.5.10-alpha.1]``.  A version
    string ends at a ``]`` (or ``-``, ``+``, whitespace…) — never at another
    digit — so requiring the next character to be ``]`` or ``-`` or ``+``
    prevents prefix collisions.
    """
    if not changelog or not version:
        return ""

    # Normalise: strip leading "v" so we can build a pattern that accepts
    # both "v0.5.1-alpha.1" and "0.5.1-alpha.1".
    bare = version.lstrip("v")
    if not bare:
        return ""

    # Escape the version string so dots / + are treated literally.
    escaped = re.escape(bare)

    # Match:  ## [  (optional v)  <version>  ]   (optional rest of header line)
    # The (?:] is a non-capturing group that requires the character immediately
    # following the version to be ] or - or + (i.e. not another digit/letter),
    # preventing "v0.5.1" from matching "v0.5.10".
    header_re = re.compile(
        r"^##\s+\[v?" + escaped + r"(?=[^\w]|\Z)",
        re.MULTILINE,
    )

    m = header_re.search(changelog)
    if not m:
        return ""

    # The section body starts after the matched header line.
    body_start = (
        changelog.index("\n", m.start()) + 1 if "\n" in changelog[m.start() :] else len(changelog)
    )

    # Find the next "## " header (start of the following section).
    next_section_re = re.compile(r"^## ", re.MULTILINE)
    n = next_section_re.search(changelog, body_start)
    body_end = n.start() if n else len(changelog)

    return changelog[body_start:body_end].strip()


#: The ``### `` subsections of a changelog version section that map to the
#: structured ``release.json`` lists the updater CLI renders as callouts.
_STRUCTURED_KEYS = ("highlights", "breaking", "migrations")


def extract_structured(section_md: str) -> dict[str, list[str]]:
    """Pull the optional ``### Highlights`` / ``### Breaking`` / ``### Migrations``
    subsections of a changelog version section into structured lists.

    Only **top-level** bullet headlines (a line beginning ``- `` or ``* `` with
    no leading indent) are collected — nested/continuation lines are skipped so
    each entry stays a concise one-liner suitable for a CLI callout. Subsection
    headings are matched case-insensitively; any missing subsection yields an
    empty list. A heading repeated within one version section **accumulates**
    in document order rather than overwriting (#1499) — union-resolving a
    CHANGELOG merge conflict routinely leaves two ``### Breaking`` blocks in
    one release, and dropping the earlier one under-reports the update in the
    operator's confirm banner. This feeds ``release.json`` (consumed by
    ``hal0.updater.updater._read_release_notes`` → the ``hal0 update`` notes
    render). The full markdown section stays the human-facing RELEASE_NOTES.md;
    this is just the machine-readable digest for the breaking/migration banner.
    """
    out: dict[str, list[str]] = {k: [] for k in _STRUCTURED_KEYS}
    if not section_md:
        return out
    # Split on "### <heading>" lines → [pre, h1, body1, h2, body2, ...].
    parts = re.split(r"^###\s+(.+?)\s*$", section_md, flags=re.MULTILINE)
    pairs = iter(parts[1:])
    for heading, body in zip(pairs, pairs, strict=False):
        key = heading.strip().lower()
        if key in out:
            out[key].extend(
                line[2:].strip() for line in body.splitlines() if line[:2] in ("- ", "* ")
            )
    return out
