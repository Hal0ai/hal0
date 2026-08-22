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


class DiscoveryError(ValueError):
    """Something under docs/<section> isn't a syncable doc."""


@dataclass(slots=True)
class Doc:
    source_path: Path
    section: str
    subsection: str | None
    slug: str  # "" for a section/subsection root (index.mdx)
    title: str
    short_title: str
    external_id: str
    body_md: str
    sidebar_order: float
    applies_to_version: str | None
    site_path: str  # old hal0.dev path, e.g. "/docs/getting-started/install/"
    warnings: list[str] = field(default_factory=list)

    @property
    def rel_key(self) -> str:
        """The external_id minus its prefix — also this doc's sort/group key."""
        return self.external_id.removeprefix(f"{EXTERNAL_ID_PREFIX}/")


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
    external_id = "/".join([EXTERNAL_ID_PREFIX, *key_parts])
    site_parts = [section] + ([subsection] if subsection else []) + ([] if is_root else [name])
    site_path = "/docs/" + "/".join(site_parts) + "/"

    short_title = frontmatter.short_title or frontmatter.title
    body_md = result.body_md
    if frontmatter.version:
        body_md = f"*Applies to: hal0 v{frontmatter.version}*\n\n{body_md}"

    return Doc(
        source_path=path,
        section=section,
        subsection=subsection,
        slug=slug,
        title=frontmatter.title,
        short_title=short_title,
        external_id=external_id,
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
