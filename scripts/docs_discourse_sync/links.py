"""Internal cross-link detection and rewriting (sync pass 2).

hal0's docs almost exclusively link to each other with Starlight's clean
absolute site paths (``[Slots](/docs/concepts/slots)``, sometimes with a
trailing slash and/or a ``#anchor``) rather than relative ``.md``/``.mdx``
file paths, so both forms are resolved here — the absolute form is what
pass 1 actually emits into every synced topic today; the relative form is
supported so a doc author who writes a relative cross-link doesn't
silently ship a dead link once it clears the transform.

A third case: a handful of docs link to ``/docs/adr/...`` — real,
git-tracked content, but not one of the synced sections (SECTIONS below),
so it has no forum topic to point at. Left as a root-relative path it
would resolve against forum.hal0.dev and 404; these are rewritten to a
GitHub blob URL at the source instead.

A fourth: a root-relative link that's missing its ``/docs`` prefix
(``docs/reference/env-vars.mdx`` has exactly one — a real typo,
``/guides/run-agents/...`` instead of ``/docs/guides/run-agents/...``).
Starlight's own dev server would have 404'd on this too, so it's
recognised and resolved the same as the correct form rather than shipped
as a second, forum-side 404.

This runs strictly after :mod:`transform` on already-transformed
Discourse markdown, once pass 1 has produced a full external_id -> topic
URL map — a link to a doc that doesn't exist yet at transform time (the
whole reason this is a *second* pass) resolves fine here. Fenced code
blocks and inline code spans are masked out before rewriting for the same
reason :mod:`transform` protects them: a documentation example that
*shows* markdown link syntax (``[Install](/docs/getting-started/install)``
as literal text, not a real link) must not get rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import transform
from .discovery import SECTIONS, source_key_for_site_path

# [text](url "optional title") — the standard inline markdown link form,
# which is the only one the transform ever emits.
_LINK_RE = re.compile(r"(?P<full>!?\[(?P<text>[^\]]*)\]\((?P<url>[^()\s]+)(?:\s+\"[^\"]*\")?\))")

_DEFAULT_GITHUB_BLOB_BASE = "https://github.com/Hal0ai/hal0/blob/main"
_ROOT_RELATIVE_SECTION_RE = re.compile(
    "^/(?:" + "|".join(re.escape(s) for s in SECTIONS) + r")(?:/|$)"
)


@dataclass(slots=True)
class RewriteResult:
    body_md: str
    changed: int
    unresolved: list[str]


def _resolve(url: str, *, current_dir: str) -> tuple[str, str, str] | None:
    """Resolve *url* (as written in a doc whose containing directory is
    *current_dir*, e.g. ``"reference/api"`` — see
    :attr:`discovery.Doc.source_dir_key`) to a ``(kind, value, anchor)``
    triple, or ``None`` if it isn't an internal docs link at all
    (external URL, mailto, image asset, bare anchor).

    ``kind`` is ``"topic"`` (``value`` is the target doc's rel_key, to be
    looked up in the sync's URL map) or ``"blob"`` (``value`` is the
    ``docs/``-relative path of a real but non-synced doc, e.g.
    ``"adr/0001-moonshine-cpu-stt-reinstatement"``).
    """
    if not url or url.startswith(("http://", "https://", "mailto:", "#", "upload://")):
        return None

    path, _, anchor = url.partition("#")

    # A root-relative link that's missing its /docs prefix (a source-doc
    # typo, but a real one — see docs/reference/env-vars.mdx) — treat it
    # the same as if it had been written correctly, rather than leaving a
    # link that was already broken on hal0.dev broken on the forum too.
    if _ROOT_RELATIVE_SECTION_RE.match(path):
        path = f"/docs{path}"

    if path.startswith("/docs/"):
        key = source_key_for_site_path(path)
        if key:
            return ("topic", key, anchor)
        rest = path.removeprefix("/docs/").strip("/")
        return ("blob", rest, anchor) if rest else None

    if path.endswith((".md", ".mdx")):
        # PurePosixPath has no filesystem-aware resolve(); collapse '..'/'.'
        # by hand against current_dir — the doc's actual containing
        # directory (docs/<section>/[<subsection>]), the same for a
        # section's index.mdx as for its sibling leaf docs since they're
        # literally sibling files on disk. (Not derived from a doc's
        # rel_key via .parent: an index doc's rel_key already has its
        # slug dropped, so .parent would strip a *second*, wrong, level.)
        parts = list(PurePosixPath(current_dir).parts)
        for part in PurePosixPath(path).with_suffix("").parts:
            if part == ".":
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        key = "/".join(parts)
        return ("topic", key, anchor) if key else None

    return None


def find_internal_link_keys(body_md: str, *, current_dir: str) -> set[str]:
    """The set of synced doc rel_keys *body_md* links to internally
    (excludes ``"blob"``-kind links, which don't participate in the
    sync's URL map, and any link inside a fenced/inline code span)."""
    protected = transform.protected_ranges(body_md)
    keys: set[str] = set()
    for m in _LINK_RE.finditer(body_md):
        if transform.is_protected(m.start(), protected):
            continue
        resolved = _resolve(m.group("url"), current_dir=current_dir)
        if resolved and resolved[0] == "topic":
            keys.add(resolved[1])
    return keys


def rewrite_internal_links(
    body_md: str,
    *,
    current_dir: str,
    url_map: dict[str, str],
    github_blob_base: str = _DEFAULT_GITHUB_BLOB_BASE,
) -> RewriteResult:
    """Rewrite every internal link in *body_md* to its Discourse topic URL
    (or, for a real-but-unsynced doc like ``docs/adr/...``, to its GitHub
    blob URL).

    A ``"topic"`` link whose target isn't in *url_map* (a doc that
    doesn't exist, or was skipped) is left as-is and reported in
    ``unresolved`` — pass 2 logs these rather than failing the whole sync
    over one stale link. A link inside a fenced code block or inline code
    span is left untouched entirely, matching :mod:`transform`'s own
    "code is opaque" rule — a doc example that *shows* markdown link
    syntax as literal text isn't a real cross-link.
    """
    changed = 0
    unresolved: list[str] = []
    protected = transform.protected_ranges(body_md)

    def _sub(m: re.Match[str]) -> str:
        nonlocal changed
        if transform.is_protected(m.start(), protected):
            return m.group("full")
        resolved = _resolve(m.group("url"), current_dir=current_dir)
        if not resolved:
            return m.group("full")
        kind, value, anchor = resolved

        if kind == "blob":
            # ADR docs are the one real case today (docs/adr/*.md) — .md
            # is that section's actual, consistent extension, not a guess
            # made per-file.
            new_url = f"{github_blob_base}/docs/{value}.md"
        else:
            topic_url = url_map.get(value)
            if not topic_url:
                unresolved.append(f"{m.group('url')} -> {value}")
                return m.group("full")
            new_url = topic_url

        if anchor:
            new_url = f"{new_url}#{anchor}"
        if new_url == m.group("url"):
            return m.group("full")
        changed += 1
        return f"[{m.group('text')}]({new_url})"

    new_body = _LINK_RE.sub(_sub, body_md)
    return RewriteResult(body_md=new_body, changed=changed, unresolved=unresolved)
