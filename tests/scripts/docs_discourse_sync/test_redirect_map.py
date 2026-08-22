"""Tests for scripts/docs_discourse_sync/redirect_map.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.docs_discourse_sync.discovery import Doc
from scripts.docs_discourse_sync.redirect_map import build_redirect_map, write_redirect_map


def _doc(rel_key: str, site_path: str) -> Doc:
    return Doc(
        source_path=Path(__file__),
        section=rel_key.split("/")[0],
        subsection=None,
        slug="",
        title="T",
        short_title="T",
        external_id="--".join(["hal0-docs", *rel_key.split("/")]),
        rel_key=rel_key,
        body_md="",
        sidebar_order=1.0,
        applies_to_version=None,
        site_path=site_path,
    )


def test_build_redirect_map_only_includes_resolved_docs() -> None:
    docs = [
        _doc("getting-started/install", "/docs/getting-started/install/"),
        _doc("getting-started/unresolved", "/docs/getting-started/unresolved/"),
    ]
    url_map = {"getting-started/install": "https://forum.hal0.dev/t/install/1"}
    mapping = build_redirect_map(docs, url_map)
    assert mapping == {"/docs/getting-started/install/": "https://forum.hal0.dev/t/install/1"}


def test_write_redirect_map_creates_parents_and_pretty_json(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "redirect-map.json"
    write_redirect_map({"/docs/x/": "https://forum.hal0.dev/t/x/1"}, out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {"/docs/x/": "https://forum.hal0.dev/t/x/1"}
    assert out.read_text(encoding="utf-8").endswith("\n")
