"""Walk ``docs/`` and build the list of :class:`Doc` to sync.

Only the five published Starlight sections are in scope — the same set
``.github/workflows/mirror-docs.yml`` mirrors into hal0-web, and in the
same order hal0-web's ``astro.config.mjs`` lists them in its Starlight
``sidebar`` (``getting-started``, ``concepts``, ``guides``, ``operate``,
``reference`` — with ``reference/api`` nesting as an automatic
subgroup). ``docs/.devdocs``, ``docs/superpowers``, and ``docs/adr`` are
never touched: they're outside every section directory below and this
walker never descends into them.

If hal0-web's sidebar order ever changes, update ``SECTIONS`` here to
match — it's a short, deliberately-duplicated constant rather than a
cross-repo read, since the sync tool runs from the hal0 checkout alone.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import frontmatter as fm
from . import transform

EXTERNAL_ID_PREFIX = "hal0-docs"
SECTIONS: tuple[str, ...] = ("getting-started", "concepts", "guides", "operate", "reference")
SECTION_TITLES: dict[str, str] = {
    "getting-started": "Start here",
    "concepts": "Concepts",
    "guides": "Guides",
    "operate": "Operate",
    "reference": "Reference",
}

# Discourse's Topic model (app/models/topic.rb) validates external_id with
# ``format: { with: /\A[\w-]+\z/ }`` — a literal '/' isn't just mis-routed
# on lookup, it's REJECTED at creation time (422) — and caps it at
# EXTERNAL_ID_MAX_LENGTH = 50. So ids are joined with '--' (a subset of
# [\w-]+), never '/', and long ones are truncated with a short stable hash
# suffix so length alone can never collide two different docs' ids.
_EXTERNAL_ID_SEP = "--"
_EXTERNAL_ID_MAX_LENGTH = 50

# Discourse's out-of-box SiteSetting default for min_topic_title_length.
# discourse_client.create_topic sends skip_validations=true, which — verified
# against PostsController#create_params — bypasses this on creation. It does
# NOT reliably bypass it on a later update: Topic's title validator re-runs
# whenever category_id changes (not just when the title itself does), and
# PostsController#update never wires a client skip_validations through to
# PostRevisor. A category-drift correction (sync.py) on a short-titled doc
# would still 422 on that specific update without this floor. 14 of the 44
# current docs — "Slots", "Agents", "Memory", "Stacks", "Services", ... —
# are under it, so this isn't a hypothetical edge case.
_DISCOURSE_MIN_TITLE_LENGTH = 15
_TITLE_PAD_SUFFIX = " (hal0 docs)"


def _discourse_safe_title(title: str) -> str:
    """Pad *title* to the Discourse title-length floor, unconditionally.

    A single ``_TITLE_PAD_SUFFIX`` appended once isn't a guarantee — it's
    tuned for the current corpus's shortest title ("CLI", 3 chars +
    12-char suffix = 15, exactly at the floor) but a 1-2 char title would
    still undershoot it (codex round 3 on PR #2004: "X" + suffix = 13).
    Reappending the same suffix until the floor is cleared is guaranteed
    regardless of how short the original title is, at the cost of visible
    repetition for a title that short — an acceptable tradeoff for a case
    that doesn't exist in the docs/ corpus today (nothing under 3 chars).
    """
    padded = title
    while len(padded) < _DISCOURSE_MIN_TITLE_LENGTH:
        padded = f"{padded}{_TITLE_PAD_SUFFIX}"
    return padded


class DiscoveryError(ValueError):
    """Something under docs/<section> isn't a syncable doc."""


def make_external_id(*parts: str) -> str:
    """Join *parts* into a Discourse-valid external_id (see the module
    docstring for the validation rule this satisfies)."""
    external_id = _EXTERNAL_ID_SEP.join((EXTERNAL_ID_PREFIX, *parts))
    if len(external_id) <= _EXTERNAL_ID_MAX_LENGTH:
        return external_id
    digest = hashlib.sha1(external_id.encode("utf-8")).hexdigest()[:8]
    keep = _EXTERNAL_ID_MAX_LENGTH - len(digest) - 1  # -1 for the '-' joiner
    return f"{external_id[:keep]}-{digest}"


@dataclass(slots=True)
class Doc:
    source_path: Path
    section: str
    subsection: str | None
    slug: str  # "" for a section/subsection root (index.mdx)
    title: str
    short_title: str
    external_id: str  # Discourse-valid id — see make_external_id()
    # "/"-joined path key (e.g. "reference/api/rest-api"), used for
    # url_map / link-resolution lookups. Deliberately independent of
    # external_id (which is '--'-joined and may be hash-truncated) rather
    # than derived from it — the two serve different, incompatible
    # constraints (Discourse's id format vs. a reversible path key).
    rel_key: str
    body_md: str
    sidebar_order: float
    applies_to_version: str | None
    site_path: str  # old hal0.dev path, e.g. "/docs/getting-started/install/"
    warnings: list[str] = field(default_factory=list)

    @property
    def source_dir_key(self) -> str:
        """The "/"-joined directory key relative links inside this doc
        resolve against. Always the doc's containing directory
        (section[/subsection]) — the same for a section's index.mdx as
        for its sibling leaf docs, since they're literally sibling files
        on disk (unlike rel_key, which drops the "index" slug and would
        double-strip a level if used as a directory key directly)."""
        parts = [self.section] + ([self.subsection] if self.subsection else [])
        return "/".join(parts)


def _rel_parts(docs_dir: Path, path: Path) -> tuple[str, ...]:
    return path.relative_to(docs_dir).with_suffix("").parts


def _load_doc(docs_dir: Path, path: Path) -> Doc:
    text = path.read_text(encoding="utf-8")
    rel_display = str(path.relative_to(docs_dir.parent))
    frontmatter, body, body_start_line = fm.split_frontmatter(text, source=rel_display)
    result = transform.transform_body(body, source=rel_display, line_offset=body_start_line)

    parts = _rel_parts(docs_dir, path)
    section = parts[0]
    if len(parts) == 2:
        subsection, name = None, parts[1]
    elif len(parts) == 3:
        subsection, name = parts[1], parts[2]
    else:
        raise DiscoveryError(
            f"{rel_display}: docs are at most two directories deep (section/[sub/]file)"
        )

    is_root = name == "index"
    slug = "" if is_root else name
    key_parts = [section] + ([subsection] if subsection else []) + ([] if is_root else [name])
    external_id = make_external_id(*key_parts)
    rel_key = "/".join(key_parts)
    site_path = "/docs/" + "/".join(key_parts) + "/"

    short_title = frontmatter.short_title or frontmatter.title
    body_md = result.body_md
    if frontmatter.version:
        body_md = f"*Applies to: hal0 v{frontmatter.version}*\n\n{body_md}"

    return Doc(
        source_path=path,
        section=section,
        subsection=subsection,
        slug=slug,
        # Padded for Discourse's title-length floor if needed (see
        # _discourse_safe_title above) — short_title, used for index-topic
        # bullet display rather than sent as a Discourse topic title
        # itself, deliberately stays the clean, unpadded value.
        title=_discourse_safe_title(frontmatter.title),
        short_title=short_title,
        external_id=external_id,
        rel_key=rel_key,
        body_md=body_md,
        sidebar_order=frontmatter.sidebar_order,
        applies_to_version=frontmatter.version,
        site_path=site_path,
        warnings=result.warnings,
    )


def discover_docs(docs_dir: Path) -> list[Doc]:
    """Discover and transform every syncable doc under *docs_dir*.

    Raises :class:`transform.TransformError` (via the first offending
    file) or :class:`frontmatter.FrontmatterError` — callers running a
    batch sync should let these abort the run rather than publish a
    partially-broken doc set.
    """
    docs: list[Doc] = []
    for section in SECTIONS:
        section_dir = docs_dir / section
        if not section_dir.is_dir():
            continue
        for path in sorted(section_dir.rglob("*.mdx")):
            docs.append(_load_doc(docs_dir, path))
    _check_unique_external_ids(docs)
    return docs


def _check_unique_external_ids(docs: list[Doc]) -> None:
    seen: dict[str, Doc] = {}
    for doc in docs:
        prior = seen.get(doc.external_id)
        if prior is not None:
            raise DiscoveryError(
                f"duplicate external_id {doc.external_id!r}: "
                f"{prior.source_path} and {doc.source_path}"
            )
        seen[doc.external_id] = doc


def source_key_for_site_path(site_path: str) -> str | None:
    """Reverse of ``Doc.site_path``: normalize a path such as
    ``/docs/reference/api/rest-api`` (any trailing slash, any anchor) to
    the ``rel_key`` a Doc would carry, or ``None`` if it isn't a docs path
    at all. Used to resolve internal cross-links in pass 2."""
    path = site_path.split("#", 1)[0].strip()
    if not path.startswith("/docs/"):
        return None
    parts = PurePosixPath(path.removeprefix("/docs/")).parts
    parts = tuple(p for p in parts if p not in ("", "."))
    if not parts or parts[0] not in SECTIONS:
        return None
    return "/".join(parts)
