"""Tests for scripts/docs_discourse_sync/frontmatter.py."""

from __future__ import annotations

import math

import pytest

from scripts.docs_discourse_sync import frontmatter as fm


def test_basic_frontmatter() -> None:
    text = (
        "---\n"
        "title: Install hal0\n"
        "description: One-line install.\n"
        "sidebar:\n"
        "  order: 10\n"
        "---\n"
        "\n"
        "Body text here.\n"
    )
    frontmatter, body, start_line = fm.split_frontmatter(text, source="x.mdx")
    assert frontmatter.title == "Install hal0"
    assert frontmatter.description == "One-line install."
    assert frontmatter.sidebar_order == 10.0
    assert frontmatter.short_title is None
    assert frontmatter.version is None
    assert body == "\nBody text here.\n"
    assert start_line == 7  # 1-indexed line right after the closing '---'


def test_missing_order_sorts_last() -> None:
    text = "---\ntitle: No order\n---\nBody\n"
    frontmatter, _, _ = fm.split_frontmatter(text, source="x.mdx")
    assert frontmatter.sidebar_order == math.inf


def test_short_title_and_version_optional_fields() -> None:
    text = '---\ntitle: Long Canonical Title\nshort_title: Short\nversion: "1.0"\n---\nBody\n'
    frontmatter, _, _ = fm.split_frontmatter(text, source="x.mdx")
    assert frontmatter.short_title == "Short"
    assert frontmatter.version == "1.0"


def test_missing_frontmatter_block_raises() -> None:
    with pytest.raises(fm.FrontmatterError, match="missing"):
        fm.split_frontmatter("# just a heading\n", source="x.mdx")


def test_missing_title_raises() -> None:
    with pytest.raises(fm.FrontmatterError, match="title"):
        fm.split_frontmatter("---\ndescription: no title\n---\nBody\n", source="x.mdx")


def test_invalid_yaml_raises() -> None:
    with pytest.raises(fm.FrontmatterError, match="YAML"):
        fm.split_frontmatter("---\ntitle: [unclosed\n---\nBody\n", source="x.mdx")


def test_non_mapping_frontmatter_raises() -> None:
    with pytest.raises(fm.FrontmatterError, match="mapping"):
        fm.split_frontmatter("---\n- just\n- a\n- list\n---\nBody\n", source="x.mdx")
