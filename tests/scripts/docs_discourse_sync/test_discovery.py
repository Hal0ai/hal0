"""Tests for scripts/docs_discourse_sync/discovery.py."""

from __future__ import annotations

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
    assert doc.external_id == "hal0-docs/getting-started/install"
    assert doc.site_path == "/docs/getting-started/install/"
    assert doc.section == "getting-started"
    assert doc.subsection is None
    assert doc.rel_key == "getting-started/install"


def test_index_doc_drops_slug_segment(tmp_path: Path) -> None:
    _write(tmp_path / "getting-started" / "index.mdx", "hal0 documentation", 0)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.external_id == "hal0-docs/getting-started"
    assert doc.site_path == "/docs/getting-started/"


def test_nested_api_subsection(tmp_path: Path) -> None:
    _write(tmp_path / "reference" / "api" / "rest-api.mdx", "REST API index", 100)
    doc = discovery.discover_docs(tmp_path)[0]
    assert doc.external_id == "hal0-docs/reference/api/rest-api"
    assert doc.site_path == "/docs/reference/api/rest-api/"
    assert doc.section == "reference"
    assert doc.subsection == "api"


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
    assert [d.external_id for d in docs] == ["hal0-docs/getting-started/install"]


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
    _write(tmp_path / "guides" / "b.mdx", "Bravo", 20)
    _write(tmp_path / "guides" / "a.mdx", "Alpha", 10)
    docs = sorted(discovery.discover_docs(tmp_path), key=lambda d: (d.sidebar_order, d.title))
    assert [d.title for d in docs] == ["Alpha", "Bravo"]
