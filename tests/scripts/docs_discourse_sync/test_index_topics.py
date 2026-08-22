"""Tests for scripts/docs_discourse_sync/index_topics.py."""

from __future__ import annotations

from pathlib import Path

from scripts.docs_discourse_sync.discovery import Doc
from scripts.docs_discourse_sync.index_topics import build_index_topics


def _doc(section: str, subsection: str | None, slug: str, title: str, order: float) -> Doc:
    key = "/".join([p for p in (section, subsection, slug) if p])
    return Doc(
        source_path=Path(__file__),  # unused by index_topics
        section=section,
        subsection=subsection,
        slug=slug,
        title=title,
        short_title=title,
        external_id=f"hal0-docs/{key}",
        body_md="",
        sidebar_order=order,
        applies_to_version=None,
        site_path=f"/docs/{key}/",
    )


def test_one_index_topic_per_section_present_in_docs() -> None:
    docs = [
        _doc("getting-started", None, "install", "Install hal0", 10),
        _doc("concepts", None, "slots", "Slots", 20),
    ]
    url_map = {
        "getting-started/install": "https://forum.hal0.dev/t/install/1",
        "concepts/slots": "https://forum.hal0.dev/t/slots/2",
    }
    topics = build_index_topics(docs, url_map)
    assert {t.external_id for t in topics} == {
        "hal0-docs-index/getting-started",
        "hal0-docs-index/concepts",
    }
    # A section with no docs (guides/operate/reference here) gets no topic.
    assert "hal0-docs-index/guides" not in {t.external_id for t in topics}


def test_bullets_in_sidebar_order() -> None:
    docs = [
        _doc("guides", None, "b", "Bravo Guide", 20),
        _doc("guides", None, "a", "Alpha Guide", 10),
    ]
    url_map = {
        "guides/a": "https://forum.hal0.dev/t/a/1",
        "guides/b": "https://forum.hal0.dev/t/b/2",
    }
    [topic] = build_index_topics(docs, url_map)
    lines = [line for line in topic.body_md.splitlines() if line.startswith("- ")]
    assert lines == [
        "- Alpha Guide: https://forum.hal0.dev/t/a/1",
        "- Bravo Guide: https://forum.hal0.dev/t/b/2",
    ]


def test_subsection_gets_its_own_heading() -> None:
    docs = [
        _doc("reference", None, "cli", "CLI reference", 10),
        _doc("reference", "api", "rest-api", "REST API index", 100),
    ]
    url_map = {
        "reference/cli": "https://forum.hal0.dev/t/cli/1",
        "reference/api/rest-api": "https://forum.hal0.dev/t/rest-api/2",
    }
    [topic] = build_index_topics(docs, url_map)
    assert topic.body_md.startswith("# Reference\n")
    assert "\n## API\n" in topic.body_md
    assert topic.body_md.index("- CLI reference:") < topic.body_md.index("## API")
    assert "- REST API index: https://forum.hal0.dev/t/rest-api/2" in topic.body_md


def test_unresolved_doc_shows_placeholder_not_a_crash() -> None:
    docs = [_doc("guides", None, "a", "Alpha", 10)]
    [topic] = build_index_topics(docs, url_map={})
    assert "unresolved" in topic.body_md
