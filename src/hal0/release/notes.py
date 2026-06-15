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
