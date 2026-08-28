"""Sync orchestration: images -> pass 1 (ensure every doc has a topic) ->
pass 2 (rewrite internal cross-links now that every topic has a URL, then
settle each doc's *final* content against the server) -> per-section
index topics -> redirect-map artifact.

The two-pass split exists because a doc's cross-links can point at a doc
that doesn't have a topic yet on a first-ever sync — pass 1 guarantees
every doc *has* a topic (creating brand-new ones with their links not yet
rewritten) before pass 2 can resolve any link against a real URL.

Content-changed detection deliberately happens in pass 2, against the
*post-link-rewrite* content, not in pass 1 against the pre-rewrite draft.
An earlier version diffed in pass 1: a doc with even one internal link
would forever alternate between "pass 1 sees the server's already-fixed-up
raw as different from this run's not-yet-rewritten draft, updates it back
to the pre-rewrite version" and "pass 2 immediately rewrites it forward
again" — a spurious update pair on *every single run*, forever, for any
doc that links to another doc.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import images, index_topics, links, redirect_map
from .discourse_client import DiscourseAPIError, DiscourseClient, Topic
from .discovery import Doc

_DRY_RUN_PENDING_MARKER = "<pending>"
# upload:// URLs only ever appear as a markdown link/image destination
# (`![alt](upload://...)`), so stop at the closing ')' as well as
# whitespace — a bare \S+ would swallow it too, corrupting everything
# after the URL in the normalized comparison string.
_UPLOAD_URL_RE = re.compile(r"upload://[^\s)]+")


@dataclass(slots=True)
class ActionLog:
    """One line of what the sync did — or, under ``--dry-run``, would do."""

    kind: str  # create | update | noop | link-rewrite | index-create | index-update | index-noop
    external_id: str
    detail: str = ""


@dataclass(slots=True)
class SyncReport:
    actions: list[ActionLog] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    url_map: dict[str, str] = field(default_factory=dict)
    redirect_map: dict[str, str] = field(default_factory=dict)

    def log(self, kind: str, external_id: str, detail: str = "") -> None:
        self.actions.append(ActionLog(kind, external_id, detail))

    def count(self, kind: str) -> int:
        return sum(1 for a in self.actions if a.kind == kind)


def _content_changed(
    existing: Topic, *, title: str, raw: str, category_id: int, dry_run: bool = False
) -> bool:
    """Whether *existing* needs updating to match *title*/*raw*/*category_id*.

    A topic that's drifted into (or was synced into) the wrong category
    needs correcting too — not just title/body — so category_id is part
    of the comparison, not just logged separately.

    Under ``dry_run``, image references in *raw* carry a synthetic
    ``upload://dry-run-<name>`` placeholder (no real upload happened),
    while *existing.raw* — fetched from a real prior sync — carries the
    real ``upload://<token>`` Discourse assigned. Diffed literally, every
    image-bearing doc would report as a perpetual "would update" in every
    `--dry-run`, forever, even with zero real changes. Both sides are
    normalized to a common placeholder before comparing, but only in
    dry-run planning — a real run always diffs the literal strings, since
    there the comparison is exact and an actual content-level image swap
    should be caught.
    """
    if existing.category_id != category_id or existing.title != title:
        return True
    existing_raw, new_raw = existing.raw.strip(), raw.strip()
    if dry_run:
        existing_raw = _UPLOAD_URL_RE.sub("upload://<planned>", existing_raw)
        new_raw = _UPLOAD_URL_RE.sub("upload://<planned>", new_raw)
    return existing_raw != new_raw


def _topic_url(topic: Topic, external_id: str, *, base_url: str) -> str:
    if topic.topic_id == -1:
        # dry-run placeholder: not a real topic yet, no slug/id to link to.
        return f"{base_url.rstrip('/')}/t/{_DRY_RUN_PENDING_MARKER}/{external_id}"
    return topic.url(base_url)


def _sync_one(
    client: DiscourseClient,
    *,
    external_id: str,
    title: str,
    raw: str,
    category_id: int,
    report: SyncReport,
    create_kind: str = "create",
    update_kind: str = "update",
    noop_kind: str = "noop",
) -> Topic:
    existing = client.resolve_topic(external_id)
    if existing is None:
        topic = client.create_topic(
            external_id=external_id, title=title, raw=raw, category_id=category_id
        )
        report.log(create_kind, external_id, title)
        return topic
    if _content_changed(
        existing, title=title, raw=raw, category_id=category_id, dry_run=client.dry_run
    ):
        client.update_topic(
            post_id=existing.first_post_id, title=title, raw=raw, category_id=category_id
        )
        report.log(update_kind, external_id, title)
        return existing
    report.log(noop_kind, external_id, "no change")
    return existing


def resolve_section_categories(
    client: DiscourseClient, *, parent_id: int, sections: Iterable[str]
) -> dict[str, int]:
    """``{section: category_id}`` for sections that have a subcategory.

    A docs section is published into its own subcategory when one exists,
    which is what lets the Docs category render as cards
    (``subcategory_list_style: boxes_with_featured_topics``) the way the
    knowledge base does. Sections with no matching subcategory are simply
    absent from the map and fall back to the parent category, so this is a
    no-op on a forum that has not been restructured -- deliberately, so
    the mapping can land before the subcategories are created.

    Both ``docs-<section>`` and a bare ``<section>`` are accepted as slugs;
    the forum's own KB tree uses the prefixed form (``kb-hardware``).
    """
    try:
        children = client.subcategory_ids(parent_id)
    except DiscourseAPIError:
        # Never fail a docs sync over a cosmetic grouping: without the
        # lookup every doc keeps going to the parent, exactly as before.
        return {}
    resolved: dict[str, int] = {}
    for section in sections:
        for slug in (f"docs-{section}", section):
            if slug in children:
                resolved[section] = children[slug]
                break
    return resolved


def sync_docs(
    docs: list[Doc],
    *,
    client: DiscourseClient,
    category_id: int,
    assets_root: Path | None = None,
    site_base_url: str = "https://hal0.dev",
) -> SyncReport:
    report = SyncReport()

    # Docs land in their section's subcategory when the forum has one, and
    # in the parent category when it does not. Index topics always stay in
    # the parent: they span sections and are what the doc-categories
    # plugin builds its sidebar from.
    section_categories = resolve_section_categories(
        client, parent_id=category_id, sections=sorted({doc.section for doc in docs})
    )
    for section, cat in sorted(section_categories.items()):
        report.log("section-category", section, f"-> category {cat}")

    def category_for(doc: Doc) -> int:
        return section_categories.get(doc.section, category_id)

    # Images resolve once per doc, before pass 1, so the raw content a
    # topic is created/updated with already carries upload:// URLs.
    prepared: dict[str, str] = {}
    for doc in docs:
        result = images.rewrite_images(
            doc.body_md, uploader=client, assets_root=assets_root, site_base_url=site_base_url
        )
        prepared[doc.external_id] = result.body_md
        report.warnings.extend(f"{doc.external_id}: {w}" for w in result.fallback_warnings)

    # Pass 1: ensure every doc has a topic. A pre-existing topic is only
    # *resolved* here, never updated — its on-server raw is what pass 2
    # diffs the final, link-rewritten content against, not this
    # pre-rewrite draft (see the module docstring for why that split
    # matters). A brand-new topic is created with this draft raw; if it
    # turns out to contain a link, pass 2 fixes it up in the same run.
    topics: dict[str, Topic] = {}
    existing_topics: dict[str, Topic | None] = {}
    for doc in docs:
        existing = client.resolve_topic(doc.external_id)
        existing_topics[doc.external_id] = existing
        if existing is None:
            topics[doc.external_id] = client.create_topic(
                external_id=doc.external_id,
                title=doc.title,
                raw=prepared[doc.external_id],
                category_id=category_for(doc),
            )
            report.log("create", doc.external_id, doc.title)
        else:
            topics[doc.external_id] = existing

    url_map = {
        doc.rel_key: _topic_url(topics[doc.external_id], doc.external_id, base_url=client.base_url)
        for doc in docs
    }
    report.url_map = url_map

    # Pass 2: rewrite internal cross-links now that every topic has a URL,
    # then settle each doc's *final* content against the server.
    for doc in docs:
        rewrite = links.rewrite_internal_links(
            prepared[doc.external_id], current_dir=doc.source_dir_key, url_map=url_map
        )
        report.warnings.extend(
            f"{doc.external_id}: unresolved link {u}" for u in rewrite.unresolved
        )
        final_raw = rewrite.body_md
        existing = existing_topics[doc.external_id]
        topic = topics[doc.external_id]

        if existing is None:
            # Just created above with the pre-rewrite draft — only needs a
            # second call if rewriting actually changed anything.
            if rewrite.changed:
                client.update_topic(
                    post_id=topic.first_post_id,
                    title=doc.title,
                    raw=final_raw,
                    category_id=category_for(doc),
                )
                report.log("link-rewrite", doc.external_id, f"{rewrite.changed} link(s) rewritten")
            continue

        if _content_changed(
            existing,
            title=doc.title,
            raw=final_raw,
            category_id=category_for(doc),
            dry_run=client.dry_run,
        ):
            client.update_topic(
                post_id=existing.first_post_id,
                title=doc.title,
                raw=final_raw,
                category_id=category_for(doc),
            )
            report.log("update", doc.external_id, doc.title)
        else:
            report.log("noop", doc.external_id, "no change")

    # Per-section index topics, built from the same URL map.
    for index_topic in index_topics.build_index_topics(docs, url_map):
        _sync_one(
            client,
            external_id=index_topic.external_id,
            title=index_topic.title,
            raw=index_topic.body_md,
            category_id=category_id,
            report=report,
            create_kind="index-create",
            update_kind="index-update",
            noop_kind="index-noop",
        )

    report.redirect_map = redirect_map.build_redirect_map(docs, url_map)
    return report
