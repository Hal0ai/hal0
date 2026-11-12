"""Every docs page must stay under the docs pipeline's hard 30k-character cap."""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
CAP = 30_000


def test_every_mdx_page_is_under_the_cap() -> None:
    pages = sorted(DOCS.rglob("*.mdx"))
    assert pages, f"no .mdx pages found under {DOCS}"
    over = {
        str(p.relative_to(DOCS.parent)): n
        for p in pages
        if (n := len(p.read_text(encoding="utf-8"))) >= CAP
    }
    assert not over, (
        f"docs pages must be under {CAP} characters; split these along a "
        f"section boundary: {over}"
    )
