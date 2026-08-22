"""Tests for scripts/docs_discourse_sync/discovery.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.docs_discourse_sync import discovery


def _write(path: Path, title: str, order: int, body: str = "Body text.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nsidebar:\n  order: {order}\n---\n\n{body}", encoding="utf-8"
    )


def test_external_id_and_site_path_for_leaf_doc(tmp_path: Path) -> None:
    _write(tmp_path / "getting-started" / "install.mdx", "Install hal0", 10)
    docs = discovery.discover_docs(tmp_path)
    assert len(docs) == 1
    doc = docs[0]
    # '--'-joined, not '/'-joined: Discourse's Topic model validates
    # external_id with `format: { with: /\A[\w-]+\z/ }` (app/models/topic.rb)
    # and REJECTS a literal '/' at creation time (422) — not just a
    # lookup-routing mismatch.
    assert doc.external_id == "hal0-docs--getting-started--install"
    assert "/" not in doc.external_id
    assert doc.site_path == "/docs/getting-started/install/"
    assert doc.section == "getting-started"
    assert doc.subsection is None
    assert doc.rel_key == "getting-started/install"
    assert doc.source_dir_key == "getting-started"


def test_index_doc_drops_slug_segment(tmp_path: Path) -> None:
    _write(tmp_path / "getting-started" / "index.mdx", "hal0 documentation", 0)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.external_id == "hal0-docs--getting-started"
    assert doc.site_path == "/docs/getting-started/"
    assert doc.rel_key == "getting-started"
    assert doc.source_dir_key == "getting-started"


def test_index_and_sibling_leaf_share_the_same_source_dir_key(tmp_path: Path) -> None:
    """docs/getting-started/index.mdx and docs/getting-started/install.mdx
    are sibling files on disk — a relative link written in either one must
    resolve the same way. source_dir_key existing at all (rather than
    deriving a doc's link-resolution directory from its rel_key, which
    drops "index" and would need re-stripping) is what keeps that true."""
    _write(tmp_path / "getting-started" / "index.mdx", "hal0 documentation", 0)
    _write(tmp_path / "getting-started" / "install.mdx", "Install hal0", 10)
    docs = {d.slug: d for d in discovery.discover_docs(tmp_path)}
    assert docs[""].source_dir_key == docs["install"].source_dir_key == "getting-started"


def test_nested_api_subsection(tmp_path: Path) -> None:
    _write(tmp_path / "reference" / "api" / "rest-api.mdx", "REST API index", 100)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.external_id == "hal0-docs--reference--api--rest-api"
    assert doc.site_path == "/docs/reference/api/rest-api/"
    assert doc.section == "reference"
    assert doc.subsection == "api"
    assert doc.source_dir_key == "reference/api"


def test_short_title_falls_back_to_title(tmp_path: Path) -> None:
    _write(tmp_path / "guides" / "configure.mdx", "Edit configuration", 100)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.short_title == "Edit configuration"


def test_version_frontmatter_emits_applies_to_notice(tmp_path: Path) -> None:
    path = tmp_path / "reference" / "env-vars.mdx"
    path.parent.mkdir(parents=True)
    path.write_text(
        '---\ntitle: Env vars\nversion: "1.0"\nsidebar:\n  order: 1\n---\n\nBody.\n',
        encoding="utf-8",
    )
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.body_md.startswith("*Applies to: hal0 v1.0*\n\n")


def test_no_version_frontmatter_has_no_notice(tmp_path: Path) -> None:
    _write(tmp_path / "reference" / "env-vars.mdx", "Env vars", 1)
    doc = discovery.discover_docs(tmp_path)[0]
    assert "Applies to" not in doc.body_md


def test_sections_outside_the_published_set_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "getting-started" / "install.mdx", "Install", 10)
    _write(tmp_path / "adr" / "0001-foo.mdx", "ADR", 1)
    _write(tmp_path / "superpowers" / "plan.mdx", "Plan", 1)
    (tmp_path / ".devdocs").mkdir()
    (tmp_path / ".devdocs" / "internal.mdx").write_text(
        "---\ntitle: Internal\n---\nBody\n", encoding="utf-8"
    )
    docs = discovery.discover_docs(tmp_path)
    assert [d.external_id for d in docs] == ["hal0-docs--getting-started--install"]


def test_duplicate_external_id_raises(tmp_path: Path) -> None:
    # A top-level "api.mdx" and a subsection root "api/index.mdx" both
    # resolve to "hal0-docs/getting-started/api" — a real collision class
    # (subsection root drops its "index" slug the same way a top-level
    # index.mdx does), not just a contrived duplicate filename.
    _write(tmp_path / "getting-started" / "api.mdx", "API leaf", 10)
    _write(tmp_path / "getting-started" / "api" / "index.mdx", "API subsection root", 10)
    with pytest.raises(discovery.DiscoveryError, match="duplicate external_id"):
        discovery.discover_docs(tmp_path)


def test_too_deep_nesting_raises(tmp_path: Path) -> None:
    _write(tmp_path / "reference" / "api" / "v2" / "deep.mdx", "Too deep", 1)
    with pytest.raises(discovery.DiscoveryError, match="two directories deep"):
        discovery.discover_docs(tmp_path)


def test_source_key_for_site_path_round_trips() -> None:
    assert discovery.source_key_for_site_path("/docs/getting-started/install/") == (
        "getting-started/install"
    )
    assert discovery.source_key_for_site_path("/docs/getting-started/") == "getting-started"
    assert discovery.source_key_for_site_path("/docs/reference/api/rest-api#section") == (
        "reference/api/rest-api"
    )
    assert discovery.source_key_for_site_path("/blog/hello") is None
    assert discovery.source_key_for_site_path("https://example.com/docs/x") is None


def test_sort_order_by_sidebar_order_then_title(tmp_path: Path) -> None:
    # Titles long enough to not trigger the min-title-length padding below
    # — keeps this test about sort order alone.
    _write(tmp_path / "guides" / "b.mdx", "Bravo Guide Doc", 20)
    _write(tmp_path / "guides" / "a.mdx", "Alpha Guide Doc", 10)
    docs = sorted(discovery.discover_docs(tmp_path), key=lambda d: (d.sidebar_order, d.title))
    assert [d.title for d in docs] == ["Alpha Guide Doc", "Bravo Guide Doc"]


# ── Discourse minimum title length ────────────────────────────────────────


def test_short_title_is_padded_for_discourse(tmp_path: Path) -> None:
    _write(tmp_path / "concepts" / "slots.mdx", "Slots", 10)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.title == "Slots (hal0 docs)"
    assert len(doc.title) >= 15


def test_title_at_or_over_min_length_is_untouched(tmp_path: Path) -> None:
    title = "Architecture Overview"  # 22 chars, over the 15-char floor
    _write(tmp_path / "concepts" / "architecture.mdx", title, 10)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.title == title


def test_short_title_padding_does_not_affect_short_title_field(tmp_path: Path) -> None:
    """short_title (index-topic bullet display) stays the clean, unpadded
    value — only the Discourse-bound title gets the length floor."""
    _write(tmp_path / "concepts" / "slots.mdx", "Slots", 10)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.short_title == "Slots"


# ── make_external_id: Discourse's own validation rule ────────────────────
# app/models/topic.rb: `validates :external_id, format: { with: /\A[\w-]+\z/ },
# length: { maximum: EXTERNAL_ID_MAX_LENGTH }` where EXTERNAL_ID_MAX_LENGTH = 50.


def test_make_external_id_never_contains_a_slash() -> None:
    external_id = discovery.make_external_id("reference", "api", "rest-api")
    assert external_id == "hal0-docs--reference--api--rest-api"
    assert "/" not in external_id


def test_make_external_id_matches_discourse_format_rule() -> None:
    external_id = discovery.make_external_id("guides", "connect-external-providers")
    assert re.fullmatch(r"[\w-]+", external_id)


def test_make_external_id_truncates_with_hash_past_the_length_cap() -> None:
    long_id = discovery.make_external_id("section", "a" * 80)
    assert len(long_id) <= 50
    assert re.fullmatch(r"[\w-]+", long_id)
    # Two different over-long inputs must not collapse to the same id.
    other_id = discovery.make_external_id("section", "b" * 80)
    assert long_id != other_id


def test_every_real_doc_external_id_is_discourse_valid() -> None:
    """The regression this whole scheme exists for: run it over the real
    docs/ corpus and confirm every generated id satisfies Discourse's
    actual validation rule, not just the handful of synthetic cases above."""
    docs_dir = Path(__file__).resolve().parents[3] / "docs"
    if not docs_dir.is_dir():
        pytest.skip("docs/ not present (unexpected checkout layout)")
    docs = discovery.discover_docs(docs_dir)
    assert len(docs) >= 40
    for doc in docs:
        assert re.fullmatch(r"[\w-]+", doc.external_id), doc.external_id
        assert len(doc.external_id) <= 50, doc.external_id
