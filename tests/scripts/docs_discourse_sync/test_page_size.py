"""Every docs page must render to a Discourse post body under the size limit.

Discourse rejects a post body over its ``max_post_length`` (32000 characters)
with a 422, and that single 422 aborts the whole ``sync-docs-discourse`` run:
``docs/reference/config-schema.mdx`` grew past it and the sync then failed on
every main commit until the page was split. This test renders each page the
way ``sync.sync_docs`` does -- transform, image rewrite, internal-link
rewrite -- and fails well before the limit, so a page gets split while it is
still a docs edit rather than a red main branch.

Fix a failure by splitting the page into two coherent reference pages (a new
page creates a new Discourse topic; the existing page keeps its topic id,
because ``external_id`` derives from the file path). Do not raise
``HEADROOM_CHARS``.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.docs_discourse_sync.discourse_client import Topic
from scripts.docs_discourse_sync.sync import DISCOURSE_MAX_POST_CHARS, rendered_bodies

from scripts.docs_discourse_sync import discovery, images

REPO_ROOT = Path(__file__).resolve().parents[3]

# Pages must stay at or under this. The gap to the hard limit absorbs what
# this test can only estimate: the real topic URLs that replace internal
# links (their slug and id are only known on the forum) and upload://
# short-URLs that replace images.
HEADROOM_CHARS = 30_000

_FORUM_BASE_URL = "https://forum.hal0.dev"
# Real topic ids are small integers today; five digits is deliberately pessimistic.
_PLACEHOLDER_TOPIC_ID = 99_999


class _NoUploads(images.ImageUploader):
    def upload(self, path: Path) -> str:  # pragma: no cover - assets_root is None
        raise AssertionError("size check must not upload images")


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _url_map(docs: list[discovery.Doc]) -> dict[str, str]:
    """Forum-shaped topic URLs, so rewritten links are about the real length."""
    return {
        doc.rel_key: Topic(
            topic_id=_PLACEHOLDER_TOPIC_ID,
            first_post_id=_PLACEHOLDER_TOPIC_ID,
            slug=_slug(doc.title),
            title=doc.title,
            raw="",
            category_id=0,
        ).url(_FORUM_BASE_URL)
        for doc in docs
    }


def rendered_length(doc: discovery.Doc, url_map: dict[str, str]) -> int:
    """The larger of the two bodies ``sync_docs`` sends Discourse for *doc*.

    A new topic is created from the pass-one body (images resolved, links not
    yet rewritten) and then updated to the link-rewritten pass-two body, and
    Discourse enforces the limit on each request. Both come from
    ``sync.rendered_bodies``, the same functions ``sync_docs`` uses.
    """
    created, updated = rendered_bodies(
        doc,
        uploader=_NoUploads(),
        assets_root=None,
        site_base_url="https://hal0.dev",
        url_map=url_map,
    )
    return max(len(created), len(updated))


def oversized_pages(
    docs: list[discovery.Doc], *, limit: int, url_map: dict[str, str] | None = None
) -> list[tuple[str, int]]:
    """``(rel_key, rendered length)`` of every page over *limit*, largest first."""
    url_map = _url_map(docs) if url_map is None else url_map
    sizes = [(doc.rel_key, rendered_length(doc, url_map)) for doc in docs]
    return sorted(((p, n) for p, n in sizes if n > limit), key=lambda item: -item[1])


def test_headroom_is_below_the_discourse_limit() -> None:
    assert HEADROOM_CHARS < DISCOURSE_MAX_POST_CHARS


def test_every_docs_page_renders_under_the_headroom() -> None:
    docs = discovery.discover_docs(REPO_ROOT / "docs")
    over = oversized_pages(docs, limit=HEADROOM_CHARS)
    assert not over, (
        f"these docs pages render to more than {HEADROOM_CHARS} characters; Discourse rejects a "
        f"post over {DISCOURSE_MAX_POST_CHARS} and the docs sync then fails for every page. "
        "Split each into two coherent reference pages (the new file gets a new topic, the old "
        "file keeps its topic):\n"
        + "\n".join(f"  docs/{key}.mdx: {length}" for key, length in over)
    )


def test_oversized_page_is_detected(tmp_path: Path) -> None:
    page = tmp_path / "reference" / "big.mdx"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Big page\nsidebar:\n  order: 1\n---\n\n" + "word " * 7_000, encoding="utf-8"
    )
    docs = discovery.discover_docs(tmp_path)
    assert [key for key, _ in oversized_pages(docs, limit=HEADROOM_CHARS)] == ["reference/big"]


def test_split_config_schema_pages_have_distinct_topics() -> None:
    # The oversize fix split config-schema into two pages. The original keeps
    # its external_id (so its Discourse topic keeps its id); the second page
    # must get an id of its own so the sync creates a new topic for it.
    docs = {d.rel_key: d for d in discovery.discover_docs(REPO_ROOT / "docs")}
    first = docs["reference/config-schema"]
    second = docs["reference/config-schema-profiles"]
    assert first.external_id == "hal0-docs--reference--config-schema"
    assert second.external_id == "hal0-docs--reference--config-schema-profiles"


def test_split_manage_slots_pages_have_distinct_topics() -> None:
    # Same shape as the config-schema split: the original keeps its topic,
    # the dashboard half gets a topic of its own.
    docs = {d.rel_key: d for d in discovery.discover_docs(REPO_ROOT / "docs")}
    assert docs["guides/manage-slots"].external_id == "hal0-docs--guides--manage-slots"
    assert (
        docs["guides/manage-slots-dashboard"].external_id
        == "hal0-docs--guides--manage-slots-dashboard"
    )


def _doc(rel_key: str, body_md: str) -> discovery.Doc:
    section, _, slug = rel_key.partition("/")
    return discovery.Doc(
        source_path=Path(f"docs/{rel_key}.mdx"),
        section=section,
        subsection=None,
        slug=slug,
        title=slug,
        short_title=slug,
        external_id=discovery.make_external_id(section, slug),
        rel_key=rel_key,
        body_md=body_md,
        sidebar_order=1,
        applies_to_version=None,
        site_path=f"/docs/{rel_key}/",
    )


def test_pass_one_body_over_the_limit_is_caught_even_when_pass_two_fits() -> None:
    # A new topic is created from the pass-one body, whose links are still the
    # long site paths. Rewriting them to short topic URLs shrinks the page, so
    # a check that only measured the final body would let this one through
    # and the sync would 422 at creation.
    target = _doc("reference/" + "t" * 100, "target")
    link = f"[x](/docs/reference/{'t' * 100}/)"
    source = _doc("reference/source", " ".join([link] * 100))
    short_urls = {target.rel_key: "https://f.dev/t/t/1", source.rel_key: "https://f.dev/t/s/2"}

    created, updated = rendered_bodies(
        source,
        uploader=_NoUploads(),
        assets_root=None,
        site_base_url="https://hal0.dev",
        url_map=short_urls,
    )
    limit = 6_000
    assert len(updated) < limit < len(created)

    over = oversized_pages([target, source], limit=limit, url_map=short_urls)
    assert [key for key, _ in over] == ["reference/source"]
    assert over[0][1] == len(created)
