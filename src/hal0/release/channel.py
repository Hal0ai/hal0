"""Release-channel helpers shared by the release + nightly workflows.

Pure functions — no I/O, stdlib only — so both .github/workflows/release.yml
and .github/workflows/nightly.yml can call them via a bare
``PYTHONPATH=src python3 -c …`` (no editable install needed), and so the
nightly version ordering stays in lock-step with the updater's
``_version_tuple`` (src/hal0/updater/updater.py).
"""

from __future__ import annotations

import re

_NIGHTLY_RE = re.compile(r"-nightly\.(\d+)")


def channel_for_tag(tag: str) -> str:
    """Return the release channel implied by a git tag.

    A version carrying a ``-nightly.<date>`` segment is on the ``nightly``
    channel; everything else (stable, alpha, beta, rc) is ``stable``.
    """
    return "nightly" if _NIGHTLY_RE.search(tag or "") else "stable"


def base_version(version: str) -> str:
    """Strip a leading ``v`` and any pre-release suffix → the base ``X.Y.Z``.

    ``v0.5.0-nightly.20260614`` → ``0.5.0``; ``0.5.0-alpha.1`` → ``0.5.0``.
    """
    v = (version or "").lstrip("v")
    return v.split("-", 1)[0]


def nightly_version(base: str, stamp: str) -> str:
    """Compose a nightly version from a base ``X.Y.Z`` and a UTC ``stamp``.

    ``stamp`` should be ``YYYYMMDDHHMMSS`` (sub-day resolution) so multiple
    cuts on the same calendar day produce strictly monotonic version strings —
    a date-only ``YYYYMMDD`` stamp would collide on same-day re-cuts and the
    updater's ``_version_tuple`` comparison would see no change and skip the
    update.  Legacy ``YYYYMMDD`` tags still sort correctly: the shorter numeric
    segment (8 digits) is always less than a 14-digit timestamp with the same
    date prefix.
    """
    return f"{base}-nightly.{stamp}"


def nightly_tag(base: str, stamp: str) -> str:
    """Compose the nightly git tag (``v`` + nightly version).

    See :func:`nightly_version` for the ``stamp`` format (``YYYYMMDDHHMMSS``).
    """
    return f"v{nightly_version(base, stamp)}"


def base_matches(pyproject_version: str, tag: str) -> bool:
    """True when ``tag`` and ``pyproject_version`` share the same base X.Y.Z.

    The relaxed gate for nightly: pyproject stays on its dev version
    (e.g. ``0.5.0-alpha.1``) while the nightly tag is
    ``v0.5.0-nightly.20260614`` — both reduce to base ``0.5.0``.
    """
    return base_version(pyproject_version) == base_version(tag)


def nightlies_to_prune(tags: list[str], keep: int = 7) -> list[str]:
    """Return the nightly tags to delete, keeping the ``keep`` most recent.

    Only ``*-nightly.<stamp>`` tags are eligible; stable/alpha/rc tags are
    never returned.  Ordering is by the numeric timestamp segment (newest
    first), which handles both legacy ``YYYYMMDD`` (8-digit) stamps and
    current ``YYYYMMDDHHMMSS`` (14-digit) stamps — a longer digit string for
    the same date always sorts higher, so old date-only tags naturally fall
    below new timestamp tags.
    """
    dated: list[tuple[int, str]] = []
    for t in tags:
        m = _NIGHTLY_RE.search(t or "")
        if m:
            dated.append((int(m.group(1)), t))
    dated.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in dated[keep:]]
