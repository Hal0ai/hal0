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
    result = links.rewrite_internal_links(body, current_dir="guides", url_map=URL_MAP)
    assert result.body_md == "See [Slots](https://forum.hal0.dev/t/slots/42) for details."
    assert result.changed == 1
    assert result.unresolved == []


def test_absolute_site_path_with_trailing_slash_and_anchor() -> None:
    body = "See [Slots](/docs/concepts/slots/#the-gpu-arbiter)."
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == "See [Slots](https://forum.hal0.dev/t/slots/42#the-gpu-arbiter)."


def test_section_root_link_resolves() -> None:
    body = "[Getting started](/docs/getting-started/)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == "[Getting started](https://forum.hal0.dev/t/getting-started/1)"


def test_relative_mdx_link_resolves_against_current_dir() -> None:
    # Written inside a doc at concepts/*.mdx, linking up and over into
    # getting-started/install.mdx.
    body = "[Install](../getting-started/install.mdx)"
    result = links.rewrite_internal_links(body, current_dir="concepts", url_map=URL_MAP)
    assert result.body_md == "[Install](https://forum.hal0.dev/t/install/2)"


def test_relative_same_dir_link_resolves() -> None:
    body = "[Install](./install.mdx)"
    result = links.rewrite_internal_links(body, current_dir="getting-started", url_map=URL_MAP)
    assert result.body_md == "[Install](https://forum.hal0.dev/t/install/2)"


def test_relative_link_from_a_section_index_resolves_to_its_sibling() -> None:
    """Regression: docs/getting-started/index.mdx and
    docs/getting-started/install.mdx are sibling *files* on disk, so a
    relative link written inside index.mdx must resolve the same way a
    link written inside install.mdx would. current_dir is what the
    caller (sync.py, via Doc.source_dir_key) is responsible for getting
    right — "getting-started" for *both* files, never index.mdx's own
    rel_key ("getting-started", already slug-dropped) with a further
    `.parent` applied on top, which used to walk one directory too high.
    """
    body = "[Install](./install.mdx)"
    # This is exactly what Doc.source_dir_key returns for the index doc
    # itself — same value a sibling leaf doc's source_dir_key would give.
    result = links.rewrite_internal_links(body, current_dir="getting-started", url_map=URL_MAP)
    assert result.body_md == "[Install](https://forum.hal0.dev/t/install/2)"
    assert result.unresolved == []


def test_external_link_is_untouched() -> None:
    body = "[GitHub](https://github.com/Hal0ai/hal0)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


def test_upload_and_anchor_only_links_are_untouched() -> None:
    body = "![img](upload://abc123) and [jump](#section)."
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


def test_unresolved_internal_link_reported_and_left_as_is() -> None:
    body = "[Missing](/docs/guides/does-not-exist)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0
    assert result.unresolved == ["/docs/guides/does-not-exist -> guides/does-not-exist"]


def test_find_internal_link_keys() -> None:
    body = "[A](/docs/concepts/slots) and [B](https://example.com) and [C](/docs/getting-started/)"
    keys = links.find_internal_link_keys(body, current_dir="x")
    assert keys == {"concepts/slots", "getting-started"}


def test_already_correct_url_reports_zero_changes() -> None:
    body = "[Slots](https://forum.hal0.dev/t/slots/42)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.changed == 0


# ── Out-of-scope /docs/ paths (e.g. docs/adr/*.md) -> GitHub blob ────────


def test_out_of_scope_docs_path_rewrites_to_github_blob() -> None:
    body = "See [ADR-0001](/docs/adr/0001-moonshine-cpu-stt-reinstatement/)."
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == (
        "See [ADR-0001]"
        "(https://github.com/Hal0ai/hal0/blob/main/docs/adr/0001-moonshine-cpu-stt-reinstatement.md)."
    )
    assert result.changed == 1
    assert result.unresolved == []  # not "unresolved" — it resolved, just not to a topic


def test_out_of_scope_docs_path_not_counted_in_internal_link_keys() -> None:
    body = "[ADR-0001](/docs/adr/0001-moonshine-cpu-stt-reinstatement/)"
    assert links.find_internal_link_keys(body, current_dir="x") == set()


def test_out_of_scope_docs_path_custom_blob_base() -> None:
    body = "[ADR](/docs/adr/0001-foo/)"
    result = links.rewrite_internal_links(
        body,
        current_dir="x",
        url_map=URL_MAP,
        github_blob_base="https://github.com/example/fork/blob/main",
    )
    assert result.body_md == "[ADR](https://github.com/example/fork/blob/main/docs/adr/0001-foo.md)"


def test_out_of_scope_docs_path_with_anchor_preserved() -> None:
    body = "[ADR](/docs/adr/0001-foo/#some-heading)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == (
        "[ADR](https://github.com/Hal0ai/hal0/blob/main/docs/adr/0001-foo.md#some-heading)"
    )


# ── Root-relative link missing its /docs prefix (a real source-doc typo) ──


def test_root_relative_link_missing_docs_prefix_resolves() -> None:
    """Regression: docs/reference/env-vars.mdx links to
    /guides/run-agents/... (missing /docs) — a real typo that would have
    404'd on hal0.dev's own dev server too. Recognised and resolved the
    same as the correctly-prefixed form rather than left root-relative
    (which would 404 against forum.hal0.dev instead)."""
    body = "See [Run agents](/guides/run-agents/#hermes-terminal-tool-off-by-default)."
    url_map = {"guides/run-agents": "https://forum.hal0.dev/t/run-agents/9"}
    result = links.rewrite_internal_links(body, current_dir="x", url_map=url_map)
    assert result.body_md == (
        "See [Run agents](https://forum.hal0.dev/t/run-agents/9#hermes-terminal-tool-off-by-default)."
    )
    assert result.unresolved == []


def test_root_relative_link_to_non_section_path_is_untouched() -> None:
    """A root-relative path whose first segment isn't a published section
    name isn't a "missing /docs prefix" typo — leave it alone rather than
    guessing."""
    body = "[Blog](/blog/some-post)"
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


# ── Code examples are never touched by link rewriting ─────────────────────


def test_link_syntax_inside_fenced_code_block_is_untouched() -> None:
    body = (
        "Example:\n\n```md\n[Install](/docs/getting-started/install)\n```\n\n"
        "Real link: [Install](/docs/getting-started/install)."
    )
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    # The fenced example keeps its literal, unrewritten markdown...
    assert "```md\n[Install](/docs/getting-started/install)\n```" in result.body_md
    # ...while the real link right after it still gets rewritten.
    assert "Real link: [Install](https://forum.hal0.dev/t/install/2)." in result.body_md
    assert result.changed == 1


def test_link_syntax_inside_inline_code_span_is_untouched() -> None:
    body = "Links look like `[text](/docs/getting-started/install)` in markdown."
    result = links.rewrite_internal_links(body, current_dir="x", url_map=URL_MAP)
    assert result.body_md == body
    assert result.changed == 0


def test_find_internal_link_keys_excludes_code_examples() -> None:
    body = "```md\n[Install](/docs/getting-started/install)\n```"
    assert links.find_internal_link_keys(body, current_dir="x") == set()
