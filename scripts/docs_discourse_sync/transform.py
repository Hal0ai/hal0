"""MDX/Starlight body -> Discourse-flavoured markdown.

Design principles (see the module-level tests for the real-doc fixtures
this was built against):

* Fenced code blocks and inline code spans are never touched by any
  substitution — they're located up front and treated as opaque during
  every later pass. Without this, prose like `` `HAL0_<DOTTED_PATH>` `` or a
  ``pct create <CTID> ...`` shell example would be misread as an unknown
  JSX tag or a stray JSX expression and the whole file would fail to sync.
* Validation runs on the *original* text before any rewriting, so a
  ``TransformError`` always cites a real line in the source ``.mdx`` file —
  not an offset into some intermediate, already-rewritten buffer.
* Only a fixed, reviewed set of Starlight components has a defined
  Discourse rendering. Anything else — an unrecognised component, or a
  bare ``{expr}`` JSX expression outside a comment — raises
  :class:`TransformError` with file+line instead of silently vanishing
  from the published doc. The one deliberate exception is
  ``<DocsSectionCards />`` (see ``_SILENT_DROP_COMPONENTS``): it is a
  self-closing, childless, website-only widget (renders the "Sections"
  card grid on hal0.dev from the live docs collection) — dropping it
  can't discard authored content because it never had any, so it's
  stripped with a logged warning rather than a hard failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Known Starlight components ──────────────────────────────────────────
# Anything JSX-tag-shaped that isn't in one of these two sets fails the
# doc loudly (see _validate_known_components).
_CONVERTED_COMPONENTS = frozenset(
    {"Aside", "Tabs", "TabItem", "Card", "CardGrid", "LinkCard", "Steps"}
)
# Self-closing, content-free components: stripped with a warning, not an
# error. Keep this set tiny and each entry justified in the docstring above.
_SILENT_DROP_COMPONENTS = frozenset({"DocsSectionCards"})
_KNOWN_COMPONENTS = _CONVERTED_COMPONENTS | _SILENT_DROP_COMPONENTS
# Among the converted set, only LinkCard is always self-closing
# (<LinkCard title="..." href="..." />) — everything else is a block
# component that must open and close.
_SELF_CLOSING_ONLY = frozenset({"LinkCard"})

_ASIDE_LABELS = {"note": "Note", "tip": "Tip", "caution": "Caution", "danger": "Danger"}

# Leading whitespace is captured and echoed in the closing-fence
# backreference: fences nested inside a <Steps> ordered-list item are
# indented (e.g. "   ```sh" under "1. **...**"), and without accounting
# for that indent here, ``^`` (true column-0 line start) never matches
# the opening backticks — the "fence" goes unrecognised and unprotected,
# its backticks then mis-pair with the *next* unrelated inline code span
# in the document and corrupt everything after it.
_CODE_FENCE_RE = re.compile(r"^([ \t]*)([`~]{3,})[^\n]*\n.*?^\1\2[ \t]*$", re.DOTALL | re.MULTILINE)
# A single soft-wrapped newline inside a code span is valid CommonMark (it
# renders as a space) and shows up throughout these hand-wrapped docs, e.g.
# `` `GET\n  /api/slots/{name}/logs` `` — so newlines are allowed *within* a
# span, just not a blank line, which would mean the backtick never closed
# and this "span" is actually two separate stray backticks in prose.
_INLINE_CODE_RE = re.compile(r"`(?:[^`\n]|\n(?!\n))+`")
_JSX_COMMENT_RE = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.DOTALL)
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w.]*)\b")
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')

_IMPORT_LINE_RE = re.compile(r"^import\s+.+?\s+from\s+['\"][^'\"]+['\"];?\s*$")
_EXPORT_LINE_RE = re.compile(r"^export\s+.+?;?\s*$")
_JS_COMMENT_LINE_RE = re.compile(r"^//.*$")
_LEADING_IMPORT_OR_EXPORT_RE = re.compile(r"^\s*(import|export)\b")

# \b after every literal tag name below: without it, "<Card ...>" happily
# matches the first 4 letters of "<CardGrid>" too (regex has no notion of
# "the tag name ends here" besides that boundary), which ate a real
# CardGrid's opening tag as a bogus empty Card and stranded its first real
# <Card>...</Card> as unconverted raw text in docs/concepts/architecture.mdx.
_ASIDE_JSX_RE = re.compile(r"<Aside\b(?P<attrs>[^>]*)>(?P<body>.*?)</Aside>", re.DOTALL)
_ASIDE_DIRECTIVE_RE = re.compile(
    r"^:::(?P<type>note|tip|caution|danger)(?:\[(?P<title>[^\]]*)\])?[ \t]*\n"
    r"(?P<body>.*?)\n:::[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_TABS_RE = re.compile(r"<Tabs\b(?:[^>]*)>(?P<body>.*?)</Tabs>", re.DOTALL)
_TABITEM_RE = re.compile(r"<TabItem\b(?P<attrs>[^>]*)>(?P<body>.*?)</TabItem>", re.DOTALL)
_CARD_RE = re.compile(r"<Card\b(?P<attrs>[^>]*)>(?P<body>.*?)</Card>", re.DOTALL)
_LINKCARD_RE = re.compile(r"<LinkCard\b(?P<attrs>[^>]*?)\s*/>")
_CARDGRID_WRAP_RE = re.compile(r"[ \t]*</?CardGrid\b[^>]*>\n?")
_STEPS_WRAP_RE = re.compile(r"[ \t]*</?Steps>\n?")
_SILENT_DROP_RE = re.compile(r"<(?P<name>" + "|".join(sorted(_SILENT_DROP_COMPONENTS)) + r")\s*/>")

_FENCE_PLACEHOLDER = "\x00FENCE{}\x00"
_INLINE_PLACEHOLDER = "\x00CODE{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00(FENCE|CODE)(\d+)\x00")


class TransformError(ValueError):
    """A doc contains a construct the transform can't safely render.

    Carries the source path and a 1-indexed line so a human can jump
    straight to the offending MDX.
    """

    def __init__(self, source: str, line: int, message: str) -> None:
        self.source = source
        self.line = line
        super().__init__(f"{source}:{line}: {message}")


@dataclass(slots=True)
class TransformResult:
    body_md: str
    warnings: list[str] = field(default_factory=list)


def _line_of(text: str, pos: int, *, offset: int) -> int:
    return text.count("\n", 0, pos) + offset


def _mask_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    """*text* with every char in *ranges* replaced by a neutral byte
    (newlines kept, so line numbers computed against the result still
    line up with the original). Used so the inline-code scan below can't
    match a backtick that's actually part of a fence delimiter or fence
    body — a plain post-hoc "does this match start inside a fence" filter
    isn't enough, because the *scan itself* still walks through the
    fence's own triple-backtick markers while looking for pairs, and can
    consume one of them as a bogus code-span delimiter — which throws off
    backtick pairing for everything after the fence, not just inside it."""
    out = list(text)
    for start, end in ranges:
        for i in range(start, end):
            if out[i] != "\n":
                out[i] = "\x01"
    return "".join(out)


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    """Byte ranges of *text* that must never be substituted into: fenced
    code blocks and inline code spans."""
    fence_ranges = [m.span() for m in _CODE_FENCE_RE.finditer(text)]
    masked = _mask_ranges(text, fence_ranges)
    ranges = list(fence_ranges)
    ranges.extend(m.span() for m in _INLINE_CODE_RE.finditer(masked))
    ranges.sort()
    return ranges


def _is_protected(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def _leading_header_span(body: str) -> tuple[int, int]:
    """``(line_count, char_end_offset)`` of the contiguous run of
    ``import``/``export``/``//`` comment/blank lines at the very top of
    *body* — MDX groups these into one JS/ESM block even when comments are
    interleaved between two imports (see ``docs/getting-started/index.mdx``,
    which has exactly that shape). The single source of truth for both
    :func:`_strip_leading_esm_header` and :func:`_validate` — computing this
    twice, independently, is exactly how a stray ``{`` inside an import's
    ``{ Foo, Bar }`` destructure list got misread as a JSX expression during
    development (the validator ran on the pre-strip body without knowing
    that span was header, not prose)."""
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if (
            stripped == ""
            or _IMPORT_LINE_RE.match(lines[i])
            or _EXPORT_LINE_RE.match(lines[i])
            or _JS_COMMENT_LINE_RE.match(stripped)
        ):
            i += 1
            continue
        break
    char_end = len("\n".join(lines[:i])) + (1 if i > 0 else 0)
    return i, char_end


def _validate(body: str, *, source: str, line_offset: int) -> None:
    """Raise :class:`TransformError` on anything the transform can't render,
    computing line numbers against the untouched original body."""
    header_lines, header_end = _leading_header_span(body)
    protected = _protected_ranges(body)
    protected.append((0, header_end))
    protected.sort()
    comment_ranges = [m.span() for m in _JSX_COMMENT_RE.finditer(body)]

    def _in_comment(pos: int) -> bool:
        return any(start <= pos < end for start, end in comment_ranges)

    # A stray import/export past the leading ESM header is unexpected —
    # fail loud rather than leak raw JS into the published doc. Skip lines
    # inside a fence/inline-code span: a shell `export FOO=bar` in a
    # ```bash example (docs/reference/model-roster-benchmark.mdx has one)
    # is not MDX/JS and must pass through untouched.
    pos = header_end
    for lineno, text in enumerate(body.split("\n")[header_lines:], start=header_lines):
        if _LEADING_IMPORT_OR_EXPORT_RE.match(text) and not _is_protected(pos, protected):
            raise TransformError(
                source,
                lineno + line_offset,
                "import/export statement outside the file's leading ESM header — "
                "the transform only strips imports at the top of the file",
            )
        pos += len(text) + 1

    # Unknown / unbalanced JSX components. LinkCard (and the silent-drop
    # set) are always self-closing and never get a depth slot — a bare
    # regex tag scan can't tell self-closing ``<X ... />`` from an opening
    # ``<X ...>`` by name alone, so peek past the tag for the ``/>``.
    depth: dict[str, int] = {}
    for m in _TAG_RE.finditer(body):
        pos = m.start()
        if _is_protected(pos, protected):
            continue
        closing, name = m.group(1), m.group(2)
        if name not in _KNOWN_COMPONENTS:
            raise TransformError(
                source,
                _line_of(body, pos, offset=line_offset),
                f"unknown JSX component <{name}> — no Discourse rendering is defined for it; "
                "either it's a typo or the transform needs to learn it",
            )
        if name in _SILENT_DROP_COMPONENTS:
            continue
        tail = re.match(r"[^>]*?(/)?>", body[m.end() :], re.DOTALL)
        self_closed = bool(tail and tail.group(1))
        line = _line_of(body, pos, offset=line_offset)
        if name in _SELF_CLOSING_ONLY:
            if closing:
                raise TransformError(source, line, f"</{name}> — {name} has no closing tag")
            if not self_closed:
                raise TransformError(
                    source, line, f"<{name} ...> must be self-closing: <{name} ... />"
                )
            continue
        if self_closed:
            raise TransformError(
                source, line, f"<{name} ... /> — self-closing form has no content to render"
            )
        depth.setdefault(name, 0)
        depth[name] += -1 if closing else 1
    unbalanced = [name for name, count in depth.items() if count != 0]
    if unbalanced:
        raise TransformError(
            source, line_offset, f"unbalanced JSX tag(s): {', '.join(sorted(unbalanced))}"
        )

    # Stray JSX expressions ({expr}) outside comments/known attrs.
    i = 0
    while True:
        pos = body.find("{", i)
        if pos == -1:
            break
        i = pos + 1
        if _is_protected(pos, protected) or _in_comment(pos):
            continue
        raise TransformError(
            source,
            _line_of(body, pos, offset=line_offset),
            "unsupported JSX expression '{...}' — dynamic content can't be statically "
            "rendered to Discourse markdown; rewrite as plain prose or extend the transform",
        )


def _strip_leading_esm_header(body: str) -> str:
    """Drop the leading ESM header block (see :func:`_leading_header_span`)."""
    header_lines, _ = _leading_header_span(body)
    return "\n".join(body.split("\n")[header_lines:])


def _parse_attrs(raw: str) -> dict[str, str]:
    return dict(_ATTR_RE.findall(raw))


def _render_aside(atype: str, title: str | None, body: str) -> str:
    label = _ASIDE_LABELS[atype]
    body = body.strip("\n")
    if title:
        return f'[details="{label}: {title}"]\n{body}\n[/details]'
    return f"[quote]\n**{label}**\n\n{body}\n[/quote]"


def _convert_asides(body: str) -> str:
    def _jsx_sub(m: re.Match[str]) -> str:
        attrs = _parse_attrs(m.group("attrs"))
        return _render_aside(attrs.get("type", "note"), attrs.get("title"), m.group("body"))

    body = _ASIDE_JSX_RE.sub(_jsx_sub, body)

    def _directive_sub(m: re.Match[str]) -> str:
        return _render_aside(m.group("type"), m.group("title"), m.group("body"))

    return _ASIDE_DIRECTIVE_RE.sub(_directive_sub, body)


def _convert_tabs(body: str) -> str:
    def _tabs_sub(m: re.Match[str]) -> str:
        sections = []
        for item in _TABITEM_RE.finditer(m.group("body")):
            attrs = _parse_attrs(item.group("attrs"))
            label = attrs.get("label", "")
            content = item.group("body").strip("\n")
            sections.append(f"### {label}\n\n{content}")
        return "\n\n".join(sections)

    return _TABS_RE.sub(_tabs_sub, body)


def _flatten_prose(text: str) -> str:
    """Collapse a Card/aside body's hand-wrapped prose into paragraphs: one
    logical line per paragraph, blank line between paragraphs — matching
    how the rest of the transform emits bullet-list content."""
    paragraphs = re.split(r"\n[ \t]*\n", text.strip("\n"))
    flat = [re.sub(r"[ \t]*\n[ \t]*", " ", p).strip() for p in paragraphs]
    return "\n\n".join(p for p in flat if p)


_CARD_LEADING_INDENT_RE = re.compile(r"^[ \t]+(?=<(?:Card|LinkCard)\b)", re.MULTILINE)


def _convert_cards(body: str) -> str:
    # Cards are indented under <CardGrid> in every source doc; strip that
    # so the bullets they become start at column 0 like every other list
    # this transform emits, rather than a stray 2-space indent that just
    # happens to still be valid CommonMark.
    body = _CARD_LEADING_INDENT_RE.sub("", body)

    def _card_sub(m: re.Match[str]) -> str:
        attrs = _parse_attrs(m.group("attrs"))
        title = attrs.get("title", "")
        content = _flatten_prose(m.group("body"))
        # Continuation paragraphs stay in the same bullet item (2-space
        # continuation indent is CommonMark's rule for list-item content).
        content = content.replace("\n\n", "\n\n  ")
        return f"- **{title}** — {content}"

    body = _CARD_RE.sub(_card_sub, body)

    def _linkcard_sub(m: re.Match[str]) -> str:
        attrs = _parse_attrs(m.group("attrs"))
        title, href, desc = attrs.get("title", ""), attrs.get("href", ""), attrs.get("description")
        return f"- [{title}]({href}): {desc}" if desc else f"- [{title}]({href})"

    body = _LINKCARD_RE.sub(_linkcard_sub, body)
    return _CARDGRID_WRAP_RE.sub("", body)


def _convert_steps(body: str) -> str:
    return _STEPS_WRAP_RE.sub("", body)


def _drop_silent_components(body: str, warnings: list[str], *, source: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        warnings.append(f"{source}: dropped website-only <{m.group('name')} /> (no static content)")
        return ""

    return _SILENT_DROP_RE.sub(_sub, body)


def _tidy_blank_lines(body: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", body).strip("\n") + "\n"


def transform_body(body: str, *, source: str, line_offset: int = 1) -> TransformResult:
    """Transform an MDX doc *body* (frontmatter already stripped) into
    Discourse-flavoured markdown. Raises :class:`TransformError` on any
    construct without a defined rendering."""
    _validate(body, source=source, line_offset=line_offset)

    warnings: list[str] = []
    working = _strip_leading_esm_header(body)

    # Protect fences/inline code before any structural rewrite touches them.
    fences: list[str] = []
    codespans: list[str] = []

    def _stash_fence(m: re.Match[str]) -> str:
        fences.append(m.group(0))
        return _FENCE_PLACEHOLDER.format(len(fences) - 1)

    working = _CODE_FENCE_RE.sub(_stash_fence, working)

    def _stash_code(m: re.Match[str]) -> str:
        codespans.append(m.group(0))
        return _INLINE_PLACEHOLDER.format(len(codespans) - 1)

    working = _INLINE_CODE_RE.sub(_stash_code, working)

    working = _JSX_COMMENT_RE.sub("", working)
    working = _drop_silent_components(working, warnings, source=source)
    working = _convert_asides(working)
    working = _convert_tabs(working)
    working = _convert_cards(working)
    working = _convert_steps(working)

    def _restore(m: re.Match[str]) -> str:
        kind, idx = m.group(1), int(m.group(2))
        return (fences if kind == "FENCE" else codespans)[idx]

    working = _PLACEHOLDER_RE.sub(_restore, working)
    working = _tidy_blank_lines(working)
    return TransformResult(body_md=working, warnings=warnings)
