"""Tests for scripts/docs_discourse_sync/links.py.

hal0's docs almost exclusively use absolute ``/docs/<section>/<slug>``
cross-links (see the real-doc excerpts in test_transform.py's fixtures);
the relative ``.md``/``.mdx`` form is supported for spec-completeness /
future-proofing even though nothing in the current corpus uses it.
"""

from __future__ import annotations

from scripts.docs_discourse_sync import links

URL_MAP = {
    "concepts/slots": "https://forum.hal0.dev/t/slots/42",
    "getting-started": "https://forum.hal0.dev/t/getting-started/1",
    "getting-started/install": "https://forum.hal0.dev/t/install/2",
}


def test_absolute_site_path_link_resolves() -> None:
    body = "See [Slots](/docs/concepts/slots) for details."
    result = links.rewrite_internal_links(body, current_key="guides/manage-slots", url_map=URL_MAP)
    assert result.body_md == "See [Slots](https://forum.hal0.dev/t/slots/42) for details."
    assert result.changed == 1
    assert result.unresolved == []


def test_absolute_site_path_with_trailing_slash_and_anchor() -> None:
    body = "See [Slots](/docs/concepts/slots/#the-gpu-arbiter)."
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.body_md == "See [Slots](https://forum.hal0.dev/t/slots/42#the-gpu-arbiter)."


def test_section_root_link_resolves() -> None:
    body = "[Getting started](/docs/getting-started/)"
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.body_md == "[Getting started](https://forum.hal0.dev/t/getting-started/1)"


def test_relative_mdx_link_resolves_against_current_doc_dir() -> None:
    body = "[Install](../getting-started/install.mdx)"
    result = links.rewrite_internal_links(body, current_key="concepts/slots", url_map=URL_MAP)
    assert result.body_md == "[Install](https://forum.hal0.dev/t/install/2)"


def test_relative_same_dir_link_resolves() -> None:
    body = "[Install](./install.mdx)"
    result = links.rewrite_internal_links(
        body, current_key="getting-started/other", url_map=URL_MAP
    )
    assert result.body_md == "[Install](https://forum.hal0.dev/t/install/2)"


def test_external_link_is_untouched() -> None:
    body = "[GitHub](https://github.com/Hal0ai/hal0)"
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


def test_upload_and_anchor_only_links_are_untouched() -> None:
    body = "![img](upload://abc123) and [jump](#section)."
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


def test_unresolved_internal_link_reported_and_left_as_is() -> None:
    body = "[Missing](/docs/guides/does-not-exist)"
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0
    assert result.unresolved == ["/docs/guides/does-not-exist -> guides/does-not-exist"]


def test_find_internal_link_keys() -> None:
    body = "[A](/docs/concepts/slots) and [B](https://example.com) and [C](/docs/getting-started/)"
    keys = links.find_internal_link_keys(body, current_key="x")
    assert keys == {"concepts/slots", "getting-started"}


def test_already_correct_url_reports_zero_changes() -> None:
    body = "[Slots](https://forum.hal0.dev/t/slots/42)"
    result = links.rewrite_internal_links(body, current_key="x", url_map=URL_MAP)
    assert result.changed == 0
