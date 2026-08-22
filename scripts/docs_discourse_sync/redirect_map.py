"""Build the old-hal0.dev-path -> forum-topic-URL redirect map artifact.

Consumed by hal0-web's 301 layer once docs stop being served from
hal0.dev/docs/** directly — keyed on the exact site path Starlight
generated for each doc (``/docs/<section>/<slug>/``, trailing slash,
matching :attr:`discovery.Doc.site_path`), so hal0-web can do a literal
lookup with no path normalization of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

from .discovery import Doc


def build_redirect_map(docs: list[Doc], url_map: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for doc in docs:
        url = url_map.get(doc.rel_key)
        if url:
            mapping[doc.site_path] = url
    return mapping


def write_redirect_map(mapping: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
