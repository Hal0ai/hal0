"""Build the per-section index topics the discourse-doc-categories sidebar
reads: one synthetic topic per published section, headed by the section
name with a nested heading per subsection (only ``reference/api`` today),
bulleting every doc's short title and topic URL in Starlight sidebar
order (``sidebar.order`` ascending, then title).

These are distinct from a section's own ``index.mdx`` overview doc (e.g.
``docs/getting-started/index.mdx``, which syncs like any other page under
its own ``hal0-docs/getting-started`` external_id) — the topics built
here are synthetic navigation aids with no source file, namespaced under
``hal0-docs-index/<section>`` so they can never collide with a real doc.
"""

from __future__ import annotations

from dataclasses import dataclass

from .discovery import SECTION_TITLES, SECTIONS, Doc, make_external_id


@dataclass(slots=True)
class IndexTopic:
    external_id: str
    title: str
    body_md: str


def _subsection_heading(name: str) -> str:
    return name.upper() if len(name) <= 3 else name.replace("-", " ").title()


def _bullet(doc: Doc, url_map: dict[str, str]) -> str:
    url = url_map.get(doc.rel_key, "(unresolved — sync this doc first)")
    return f"- {doc.short_title}: {url}"


def build_index_topics(docs: list[Doc], url_map: dict[str, str]) -> list[IndexTopic]:
    topics: list[IndexTopic] = []
    for section in SECTIONS:
        section_docs = [d for d in docs if d.section == section]
        if not section_docs:
            continue

        lines = [f"# {SECTION_TITLES[section]}", ""]
        top_level = sorted(
            (d for d in section_docs if d.subsection is None),
            key=lambda d: (d.sidebar_order, d.title),
        )
        lines.extend(_bullet(d, url_map) for d in top_level)

        subsections = sorted({d.subsection for d in section_docs if d.subsection})
        for sub in subsections:
            sub_docs = sorted(
                (d for d in section_docs if d.subsection == sub),
                key=lambda d: (d.sidebar_order, d.title),
            )
            lines.append("")
            lines.append(f"## {_subsection_heading(sub)}")
            lines.append("")
            lines.extend(_bullet(d, url_map) for d in sub_docs)

        topics.append(
            IndexTopic(
                external_id=make_external_id("index", section),
                title=f"hal0 docs: {SECTION_TITLES[section]}",
                body_md="\n".join(lines).strip("\n") + "\n",
            )
        )
    return topics
