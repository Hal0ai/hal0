"""Internal cross-link detection and rewriting (sync pass 2).

hal0's docs almost exclusively link to each other with Starlight's clean
absolute site paths (``[Slots](/docs/concepts/slots)``, sometimes with a
trailing slash and/or a ``#anchor``) rather than relative ``.md``/``.mdx``
file paths, so both forms are resolved here — the absolute form is what
pass 1 actually emits into every synced topic today; the relative form is
supported so a doc author who writes a relative cross-link doesn't
silently ship a dead link once it clears the transform.

This runs strictly after :mod:`transform` on already-transformed
Discourse markdown, once pass 1 has produced a full external_id -> topic
URL map — a link to a doc that doesn't exist yet at transform time (the
whole reason this is a *second* pass) resolves fine here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .discovery import source_key_for_site_path

# [text](url "optional title") — the standard inline markdown link form,
# which is the only one the transform ever emits.
_LINK_RE = re.compile(r"(?P<full>!?\[(?P<text>[^\]]*)\]\((?P<url>[^()\s]+)(?:\s+\"[^\"]*\")?\))")


@dataclass(slots=True)
class RewriteResult:
    body_md: str
    changed: int
    unresolved: list[str]


def _resolve(url: str, *, current_key: str) -> tuple[str, str] | None:
    """Resolve *url* (as written in a doc whose rel_key is *current_key*)
    to ``(target_rel_key, anchor)``, or ``None`` if it isn't an internal
    docs link at all (external URL, mailto, image asset, bare anchor)."""
    if not url or url.startswith(("http://", "https://", "mailto:", "#", "upload://")):
        return None

    path, _, anchor = url.partition("#")

    if path.startswith("/docs/"):
        key = source_key_for_site_path(path)
        return (key, anchor) if key else None

    if path.endswith((".md", ".mdx")):
        # PurePosixPath has no filesystem-aware resolve(); collapse '..'/'.'
        # by hand against the docs-relative directory this link's doc lives in.
        current_dir = PurePosixPath(current_key).parent
        parts = list(current_dir.parts)
        for part in PurePosixPath(path).with_suffix("").parts:
            if part == ".":
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        key = "/".join(parts)
        return (key, anchor) if key else None

    return None


def find_internal_link_keys(body_md: str, *, current_key: str) -> set[str]:
    """The set of doc rel_keys *body_md* links to internally."""
    keys: set[str] = set()
    for m in _LINK_RE.finditer(body_md):
        resolved = _resolve(m.group("url"), current_key=current_key)
        if resolved:
            keys.add(resolved[0])
    return keys


def rewrite_internal_links(
    body_md: str, *, current_key: str, url_map: dict[str, str]
) -> RewriteResult:
    """Rewrite every internal link in *body_md* to its Discourse topic URL.

    A link whose target isn't in *url_map* (a doc that doesn't exist, or
    was skipped) is left as-is and reported in ``unresolved`` — pass 2
    logs these rather than failing the whole sync over one stale link.
    """
    changed = 0
    unresolved: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        nonlocal changed
        resolved = _resolve(m.group("url"), current_key=current_key)
        if not resolved:
            return m.group("full")
        target_key, anchor = resolved
        topic_url = url_map.get(target_key)
        if not topic_url:
            unresolved.append(f"{m.group('url')} -> {target_key}")
            return m.group("full")
        new_url = f"{topic_url}#{anchor}" if anchor else topic_url
        if new_url == m.group("url"):
            return m.group("full")
        changed += 1
        return f"[{m.group('text')}]({new_url})"

    new_body = _LINK_RE.sub(_sub, body_md)
    return RewriteResult(body_md=new_body, changed=changed, unresolved=unresolved)
