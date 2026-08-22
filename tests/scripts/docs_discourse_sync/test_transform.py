"""Tests for scripts/docs_discourse_sync/transform.py.

Fixture-backed tests use verbatim excerpts pulled from the real docs under
``fixtures/`` (see that directory) — regressions here caught three real bugs
against the actual corpus during development: fences indented under
<Steps> not being recognised (their backticks then corrupted every later
inline code span in the file), a bare `<Card ...>` regex also matching the
first four letters of `<CardGrid>`, and the leading-ESM-header validator
running before the header was excluded from the stray-`{`-expression scan.
``test_transform_every_real_doc`` guards all three (and anything like them)
directly against the live ``docs/`` tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.docs_discourse_sync import frontmatter as fm
from scripts.docs_discourse_sync import transform

FIXTURES = Path(__file__).parent / "fixtures"
REPO_DOCS = Path(__file__).resolve().parents[3] / "docs"


def _transform_fixture(name: str) -> transform.TransformResult:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    _frontmatter, body, start_line = fm.split_frontmatter(text, source=name)
    return transform.transform_body(body, source=name, line_offset=start_line)


# ── Real-doc-derived fixtures ────────────────────────────────────────────


def test_aside_danger_with_title_becomes_details() -> None:
    result = _transform_fixture("aside_danger.mdx")
    assert '[details="Danger: Experimental and unsupported"]' in result.body_md
    assert "[/details]" in result.body_md
    assert "**out of scope for hal0 v1**" in result.body_md  # inner markdown preserved
    # Internal links inside the aside survive verbatim (pass 2 rewrites them later).
    assert "[bare metal](/docs/getting-started/bare-metal/)" in result.body_md
    assert "<Aside" not in result.body_md
    assert "import " not in result.body_md


def test_aside_note_without_title_becomes_bold_quote() -> None:
    result = _transform_fixture("aside_note.mdx")
    assert "[quote]\n**Note**\n\n" in result.body_md
    assert "[/quote]" in result.body_md
    assert "[details=" not in result.body_md
    # The image reference before the aside is untouched by the transform
    # (image upload rewriting is sync.py's job, not transform's).
    assert "![The dashboard Agents view" in result.body_md


def test_steps_becomes_plain_ordered_list_with_fence_intact() -> None:
    result = _transform_fixture("tabs_and_steps.mdx")
    assert "<Steps>" not in result.body_md
    assert "</Steps>" not in result.body_md
    assert "1. Make sure the OpenWebUI unit is running" in result.body_md
    assert "```sh\n   systemctl status hal0-openwebui\n   ```" in result.body_md


def test_tabs_become_sequential_headings_with_fences_intact() -> None:
    result = _transform_fixture("tabs_and_steps.mdx")
    assert "### Non-streaming" in result.body_md
    assert "### Streaming" in result.body_md
    assert "<Tabs>" not in result.body_md
    assert "<TabItem" not in result.body_md
    # Fenced JSON bodies (with their own braces) pass through untouched.
    assert '"model": "agent",' in result.body_md
    assert '"stream": true,' in result.body_md
    # Ordering: Non-streaming's heading precedes Streaming's.
    assert result.body_md.index("### Non-streaming") < result.body_md.index("### Streaming")


def test_cardgrid_of_cards_becomes_bullet_list() -> None:
    result = _transform_fixture("cards_grid.mdx")
    assert "<CardGrid>" not in result.body_md
    assert "<Card" not in result.body_md
    assert "- **hal0-api** — A FastAPI application" in result.body_md
    assert "- **Slots** — Each slot" in result.body_md
    assert "- **Stacks** — A named, portable bundle" in result.body_md
    # A Card's internal doc link survives for pass-2 link rewrite.
    assert "[Stacks](/docs/concepts/stacks)" in result.body_md
    # Bullets start at column 0, not indented under the stripped CardGrid.
    assert "\n  - **hal0-api**" not in result.body_md


def test_cardgrid_of_linkcards_becomes_markdown_link_list() -> None:
    result = _transform_fixture("cards_grid.mdx")
    assert (
        "- [Slots](/docs/concepts/slots): The lifecycle, single-flight dispatch, "
        "and the write-boundary partition." in result.body_md
    )
    assert "<LinkCard" not in result.body_md


def test_leading_esm_header_with_interleaved_comments_and_second_import() -> None:
    """docs/getting-started/index.mdx's exact shape: import, six `//`
    comment lines, a second import, all with no blank line between them —
    MDX treats that whole run as one ESM block, not markdown prose."""
    result = _transform_fixture("esm_header_with_comments.mdx")
    assert "import " not in result.body_md
    assert "// Rendered by the website only" not in result.body_md
    assert result.body_md.startswith("**hal0** turns a Linux box")


def test_docs_section_cards_dropped_with_warning() -> None:
    result = _transform_fixture("esm_header_with_comments.mdx")
    assert "<DocsSectionCards" not in result.body_md
    assert any("DocsSectionCards" in w for w in result.warnings)


# ── Synthetic edge cases ─────────────────────────────────────────────────


def test_colon_fence_aside_directive_supported() -> None:
    body = ":::tip[Watch this]\nDo the thing carefully.\n:::\n"
    result = transform.transform_body(body, source="x.mdx")
    assert '[details="Tip: Watch this"]' in result.body_md
    assert "Do the thing carefully." in result.body_md


def test_colon_fence_aside_without_title() -> None:
    body = ":::caution\nBe careful.\n:::\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "[quote]\n**Caution**\n\nBe careful.\n[/quote]" in result.body_md


def test_fenced_code_block_passes_through_untouched() -> None:
    body = "Some text.\n\n```python\nimport os\nvalue = {1, 2, 3}\n```\n\nMore text.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "```python\nimport os\nvalue = {1, 2, 3}\n```" in result.body_md


def test_fenced_code_blank_lines_are_not_collapsed() -> None:
    """Regression: blank-line tidying used to run *after* fence
    restoration, so a fenced example with two consecutive blank lines
    (three-plus newlines) got globally collapsed right along with
    surrounding prose — violating the module's own "fences are never
    touched" invariant."""
    body = "Text.\n\n```python\ndef a():\n    pass\n\n\ndef b():\n    pass\n```\n\nMore.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "def a():\n    pass\n\n\ndef b():\n    pass" in result.body_md


def test_single_quoted_jsx_attributes_parsed_like_double_quoted() -> None:
    body = "<LinkCard title='Slots' href='/docs/concepts/slots' description='The lifecycle.' />\n"
    result = transform.transform_body(body, source="x.mdx")
    assert result.body_md.strip() == "- [Slots](/docs/concepts/slots): The lifecycle."


def test_single_quoted_aside_attributes_parsed_like_double_quoted() -> None:
    body = "<Aside type='danger' title='Stop'>\nRead this first.\n</Aside>\n"
    result = transform.transform_body(body, source="x.mdx")
    assert '[details="Danger: Stop"]' in result.body_md
    assert "Read this first." in result.body_md


def test_mixed_quote_styles_on_the_same_tag_both_parse() -> None:
    body = "<LinkCard title='Slots' href=\"/docs/concepts/slots\" />\n"
    result = transform.transform_body(body, source="x.mdx")
    assert result.body_md.strip() == "- [Slots](/docs/concepts/slots)"


def test_double_backtick_span_wrapping_a_literal_backtick_is_protected() -> None:
    """CommonMark: a code span's delimiter is a run of N backticks, closed
    by the *next run of exactly N backticks* — not just "the next
    backtick". A naive single-backtick scan mis-reads the outer ``` `` ```
    delimiters here as their own (bogus) single-backtick spans instead of
    one double-backtick span wrapping a literal backtick, which then
    desyncs pairing for every code span after it in the file — the same
    corruption class an unrecognised fence caused before it was protected
    too. Also checks the very next, ordinary single-backtick span still
    pairs correctly afterwards (proof pairing didn't desync)."""
    body = "Use `` `not-a-tag` `` literally, then `a normal span` right after.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert result.body_md.strip() == body.strip()  # entirely code/prose, nothing to rewrite
    assert "`` `not-a-tag` ``" in result.body_md
    assert "`a normal span`" in result.body_md


def test_brace_after_a_double_backtick_span_is_still_validated() -> None:
    """A stray {expr} placed *after* a `` double-backtick `` span must
    still be caught — proof the double-backtick span's protected range
    ends where it should, not somewhere pairing-corrupted."""
    body = "Use `` `not-a-tag` `` here, then a real stray {brace} outside any backticks.\n"
    with pytest.raises(transform.TransformError, match="JSX expression"):
        transform.transform_body(body, source="x.mdx")


def test_double_backtick_span_without_a_closer_is_literal_text() -> None:
    body = "This has a stray `` double backtick run with no closer.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert result.body_md.strip() == body.strip()


def test_indented_fence_under_ordered_list_is_protected() -> None:
    """Regression: an unrecognised indented fence's backticks used to
    mis-pair with the *next* inline code span in the document, corrupting
    everything after it (see docs/operate/auth.mdx's Steps block)."""
    body = (
        "1. Run it:\n"
        "   ```sh\n"
        "   hal0 auth rotate admin\n"
        "   ```\n"
        '   Then check `/etc/hal0/api.env` and `{"require_auth": true}` by hand.\n'
    )
    result = transform.transform_body(body, source="x.mdx")
    assert "```sh\n   hal0 auth rotate admin\n   ```" in result.body_md
    assert "`/etc/hal0/api.env`" in result.body_md
    assert '`{"require_auth": true}`' in result.body_md


def test_inline_code_span_with_soft_wrap_newline_is_protected() -> None:
    body = "See `GET\n  /api/slots/{name}/logs` for details.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "{name}" in result.body_md  # not flagged as a JSX expression


def test_import_export_stripped_from_leading_header() -> None:
    body = "import { Foo } from '@astrojs/starlight/components';\n\nHello world.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert result.body_md.strip() == "Hello world."


def test_unknown_jsx_component_fails_loudly_with_file_and_line() -> None:
    body = "Intro.\n\n<Mystery>content</Mystery>\n"
    with pytest.raises(transform.TransformError) as exc_info:
        transform.transform_body(body, source="docs/x.mdx", line_offset=5)
    err = exc_info.value
    assert err.source == "docs/x.mdx"
    assert err.line == 5 + 2  # the <Mystery> line, 0-indexed line 2 within body
    assert "Mystery" in str(err)


def test_bare_jsx_expression_fails_loudly() -> None:
    body = "Some prose with a stray {expression} in it.\n"
    with pytest.raises(transform.TransformError, match="JSX expression"):
        transform.transform_body(body, source="x.mdx")


def test_jsx_comment_is_silently_stripped_not_an_error() -> None:
    body = "Before.\n\n{/* internal note, never rendered */}\n\nAfter.\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "internal note" not in result.body_md
    assert "Before." in result.body_md
    assert "After." in result.body_md


def test_unbalanced_aside_tag_fails_loudly() -> None:
    body = '<Aside type="note">\nno closing tag\n'
    with pytest.raises(transform.TransformError, match="unbalanced"):
        transform.transform_body(body, source="x.mdx")


def test_linkcard_without_self_close_fails_loudly() -> None:
    body = '<LinkCard title="X" href="/docs/y">\n'
    with pytest.raises(transform.TransformError, match="self-closing"):
        transform.transform_body(body, source="x.mdx")


def test_mid_document_import_outside_header_fails_loudly() -> None:
    body = "Prose first.\n\nimport { Late } from 'somewhere';\n"
    with pytest.raises(transform.TransformError, match="leading ESM header"):
        transform.transform_body(body, source="x.mdx")


def test_shell_export_inside_fence_is_not_flagged_as_js_export() -> None:
    body = "```bash\nexport HAL0_BENCH_TOKEN=secret\nhal0 bench upload run.tar.gz\n```\n"
    result = transform.transform_body(body, source="x.mdx")
    assert "export HAL0_BENCH_TOKEN=secret" in result.body_md


# ── Full-corpus regression ────────────────────────────────────────────────


@pytest.mark.skipif(not REPO_DOCS.is_dir(), reason="docs/ not present (unexpected checkout layout)")
def test_transform_every_real_doc() -> None:
    """Every real, in-scope .mdx file transforms without raising. This is
    the test that actually caught the fence-indentation, Card/CardGrid
    prefix, and header-vs-brace-scan-ordering bugs during development —
    hand-picked fixtures alone would not have."""
    failures: list[str] = []
    count = 0
    for section in ("getting-started", "concepts", "guides", "operate", "reference"):
        section_dir = REPO_DOCS / section
        if not section_dir.is_dir():
            continue
        for path in sorted(section_dir.rglob("*.mdx")):
            count += 1
            text = path.read_text(encoding="utf-8")
            try:
                _frontmatter, body, start_line = fm.split_frontmatter(text, source=str(path))
                transform.transform_body(body, source=str(path), line_offset=start_line)
            except (fm.FrontmatterError, transform.TransformError) as exc:
                failures.append(str(exc))
    assert count >= 40, f"expected the full published docs corpus, only found {count}"
    assert not failures, "\n".join(failures)
