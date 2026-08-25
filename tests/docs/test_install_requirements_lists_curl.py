"""``docs/getting-started/install.mdx`` must list ``curl`` as a requirement.

The install page leads with:

    curl -fsSL https://hal0.dev/install.sh | sudo bash

but its **Requirements** section never mentioned ``curl`` (or
``ca-certificates``), even though the documented one-liner cannot run without
it. On a minimal image (e.g. a bare Proxmox LXC template) that command dies
with a bare ``curl: command not found`` from the user's own shell — before
hal0 ever runs, so there is nothing hal0 can print to explain it. Found while
standing up the v1.0.0-rc.5 validation box (issue #1843).

This pins the fix: the Requirements section must call out curl (and
ca-certificates) explicitly, in the same section as the other prerequisites.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_MDX = _REPO_ROOT / "docs" / "getting-started" / "install.mdx"


def _requirements_section(text: str) -> str:
    """Slice out the ``## Requirements`` section (up to the next ``## `` heading)."""
    start = text.index("## Requirements")
    rest = text[start:]
    next_heading = rest.find("\n## ", 1)
    return rest if next_heading == -1 else rest[:next_heading]


def test_requirements_section_mentions_curl() -> None:
    assert _INSTALL_MDX.exists(), f"missing {_INSTALL_MDX}"
    text = _INSTALL_MDX.read_text(encoding="utf-8")

    section = _requirements_section(text)

    assert "curl" in section, (
        "Requirements section must call out curl — the bootstrap one-liner "
        "(`curl -fsSL https://hal0.dev/install.sh | sudo bash`) hard-requires "
        "it and fails with a bare 'command not found' on minimal images "
        "otherwise (issue #1843)."
    )
