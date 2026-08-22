"""Tests for scripts/docs_discourse_sync/sync.py — the two-pass planning
engine. All HTTP is mocked (``httpx.MockTransport``); nothing here ever
touches a real network, matching the task's "no live forum" constraint.

``FakeForum`` is a small in-memory stand-in for a Discourse instance: it
tracks created/edited topics by external_id so a *second* ``sync_docs``
call against the same state is a genuine idempotency check, not just a
second blind run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from scripts.docs_discourse_sync.discourse_client import DiscourseClient
from scripts.docs_discourse_sync.discovery import Doc
from scripts.docs_discourse_sync.sync import sync_docs


def _doc(key: str, title: str, body_md: str, *, order: float = 10.0) -> Doc:
    """*key* is the "/"-joined rel_key, e.g. "guides/a" or "guides/a/b"
    (subsection) or just "guides" (a section root/index doc)."""
    parts = key.split("/")
    section = parts[0]
    if len(parts) == 3:
        subsection, slug = parts[1], parts[2]
    elif len(parts) == 2:
        subsection, slug = None, parts[1]
    else:
        subsection, slug = None, ""
    return Doc(
        source_path=Path(f"docs/{key}.mdx"),
        section=section,
        subsection=subsection,
        slug=slug,
        title=title,
        short_title=title,
        external_id="--".join(["hal0-docs", *parts]),
        rel_key=key,
        body_md=body_md,
        sidebar_order=order,
        applies_to_version=None,
        site_path=f"/docs/{key}/",
    )


class FakeForum:
    """Enough of the Discourse admin API for the sync engine's own calls:
    GET resolve-by-external_id, POST create, PUT edit, POST upload."""

    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}  # external_id -> topic state
        self._next_id = 100
        self.request_log: list[tuple[str, str]] = []

    def _alloc(self) -> int:
        self._next_id += 1
        return self._next_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.request_log.append((request.method, request.url.path))

        if request.method == "GET" and request.url.path.startswith("/t/external_id/"):
            external_id = request.url.path.removeprefix("/t/external_id/").removesuffix(".json")
            topic = self.topics.get(external_id)
            if topic is None:
                return httpx.Response(404, json={"error_type": "not_found"})
            return httpx.Response(
                200,
                json={
                    "id": topic["topic_id"],
                    "slug": topic["slug"],
                    "title": topic["title"],
                    "category_id": topic["category_id"],
                    "post_stream": {"posts": [{"id": topic["post_id"], "raw": topic["raw"]}]},
                },
            )

        if request.method == "POST" and request.url.path == "/posts.json":
            payload = json.loads(request.content)
            topic_id, post_id = self._alloc(), self._alloc()
            slug = payload["external_id"].rsplit("--", 1)[-1]
            self.topics[payload["external_id"]] = {
                "topic_id": topic_id,
                "post_id": post_id,
                "slug": slug,
                "title": payload["title"],
                "raw": payload["raw"],
                "category_id": payload["category"],
            }
            return httpx.Response(
                200, json={"id": post_id, "topic_id": topic_id, "topic_slug": slug}
            )

        if request.method == "PUT" and request.url.path.startswith("/posts/"):
            post_id = int(request.url.path.removeprefix("/posts/").removesuffix(".json"))
            payload = json.loads(request.content)
            for topic in self.topics.values():
                if topic["post_id"] == post_id:
                    topic["raw"] = payload["post"]["raw"]
                    topic["title"] = payload["title"]
                    topic["category_id"] = payload["category"]
                    return httpx.Response(200, json={})
            return httpx.Response(404, json={"error_type": "not_found"})

        if request.method == "POST" and request.url.path == "/uploads.json":
            return httpx.Response(200, json={"short_url": "upload://fake-upload"})

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")


def _client(forum: FakeForum, **kwargs) -> DiscourseClient:
    return DiscourseClient(
        base_url="https://forum.hal0.dev",
        api_key="k",
        api_username="u",
        transport=httpx.MockTransport(forum.handler),
        requests_per_minute=1_000_000,
        **kwargs,
    )


def test_first_sync_creates_every_topic_and_index() -> None:
    docs = [
        _doc("guides/a", "Alpha", "Alpha body.", order=10),
        _doc("guides/b", "Bravo", "Bravo body, see [Alpha](/docs/guides/a).", order=20),
    ]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    kinds = [a.kind for a in report.actions]
    assert kinds.count("create") == 2
    assert kinds.count("index-create") == 1
    assert "hal0-docs--guides--a" in forum.topics
    assert "hal0-docs--guides--b" in forum.topics
    assert "hal0-docs--index--guides" in forum.topics
    assert all("/" not in ext_id for ext_id in forum.topics)


def test_pass_two_rewrites_cross_link_after_pass_one_creates_both_topics() -> None:
    docs = [
        _doc("guides/a", "Alpha", "Alpha body.", order=10),
        _doc("guides/b", "Bravo", "See [Alpha](/docs/guides/a).", order=20),
    ]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    alpha_url = report.url_map["guides/a"]
    assert alpha_url in forum.topics["hal0-docs--guides--b"]["raw"]
    assert "/docs/guides/a" not in forum.topics["hal0-docs--guides--b"]["raw"]
    link_rewrites = [a for a in report.actions if a.kind == "link-rewrite"]
    assert {a.external_id for a in link_rewrites} == {"hal0-docs--guides--b"}


def test_relative_link_from_a_section_index_resolves_to_its_sibling() -> None:
    """Regression: a relative link written inside a section's index doc
    (rel_key == just the section name, e.g. "guides") used to resolve one
    directory too high, because current_dir was derived by taking
    .parent of that already-slug-dropped rel_key. sync.py now passes
    Doc.source_dir_key instead, which is "guides" for both the index doc
    and its sibling — not "guides".parent == ".".
    """
    docs = [
        _doc("guides", "Guides overview", "See [Sibling](./sibling.mdx)."),
        _doc("guides/sibling", "Sibling", "Sibling body."),
    ]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    assert not any("unresolved link" in w for w in report.warnings)
    sibling_url = report.url_map["guides/sibling"]
    assert sibling_url in forum.topics["hal0-docs--guides"]["raw"]


def test_out_of_scope_docs_link_rewrites_to_github_blob() -> None:
    docs = [
        _doc(
            "guides/voice-stt-tts",
            "Voice",
            "See [ADR-0001](/docs/adr/0001-moonshine-cpu-stt-reinstatement/).",
        )
    ]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    raw = forum.topics["hal0-docs--guides--voice-stt-tts"]["raw"]
    assert (
        "https://github.com/Hal0ai/hal0/blob/main/docs/adr/0001-moonshine-cpu-stt-reinstatement.md"
        in raw
    )
    # The un-rewritten root-relative form (which would 404 on the forum,
    # since forum.hal0.dev knows nothing about /docs/) is gone — not just
    # "the substring /docs/adr/ is absent", which the blob URL itself
    # legitimately still contains.
    assert "](/docs/adr/" not in raw
    assert not any("unresolved link" in w for w in report.warnings)


def test_second_sync_with_no_changes_is_all_noop() -> None:
    docs = [
        _doc("guides/a", "Alpha", "Alpha body.", order=10),
        _doc("guides/b", "Bravo", "See [Alpha](/docs/guides/a).", order=20),
    ]
    forum = FakeForum()
    with _client(forum) as client:
        sync_docs(docs, client=client, category_id=7)

    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    kinds = {a.kind for a in report.actions}
    assert kinds == {"noop", "index-noop"}
    assert report.count("create") == 0
    assert report.count("update") == 0
    assert report.count("link-rewrite") == 0


def test_changed_doc_triggers_update_on_second_sync() -> None:
    docs = [_doc("guides/a", "Alpha", "Alpha body v1.", order=10)]
    forum = FakeForum()
    with _client(forum) as client:
        sync_docs(docs, client=client, category_id=7)

    docs[0].body_md = "Alpha body v2, changed."
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    assert report.count("update") == 1
    assert forum.topics["hal0-docs--guides--a"]["raw"] == "Alpha body v2, changed."


def test_category_change_triggers_update() -> None:
    """A topic that drifted into (or was synced into) the wrong category
    must be corrected even when title/body are unchanged — category_id is
    part of the change-detection predicate, not just logged separately."""
    docs = [_doc("guides/a", "Alpha", "Alpha body.", order=10)]
    forum = FakeForum()
    with _client(forum) as client:
        sync_docs(docs, client=client, category_id=7)
    assert forum.topics["hal0-docs--guides--a"]["category_id"] == 7

    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=9)  # same content, new category

    assert report.count("update") == 1
    assert report.count("noop") == 0
    assert forum.topics["hal0-docs--guides--a"]["category_id"] == 9


def test_dry_run_never_mutates() -> None:
    docs = [_doc("guides/a", "Alpha", "Alpha body.", order=10)]
    forum = FakeForum()
    with _client(forum, dry_run=True) as client:
        report = sync_docs(docs, client=client, category_id=7)

    assert report.count("create") == 1
    assert "hal0-docs--guides--a" not in forum.topics  # nothing was actually created
    methods_used = {method for method, _ in forum.request_log}
    assert methods_used == {"GET"}  # only read-only resolve calls happened


def test_dry_run_does_not_perpetually_report_image_bearing_doc_as_changed(tmp_path: Path) -> None:
    """Regression: a real sync's upload gets a real upload://<token>
    Discourse assigned; a --dry-run's *planning* draft carries a synthetic
    upload://dry-run-<name> placeholder (no real upload happens under
    dry_run). Diffed literally, that mismatch alone used to make every
    image-bearing doc report "would update" on every single --dry-run,
    even with zero real changes.
    """
    assets_root = tmp_path / "assets"
    (assets_root / "screenshots").mkdir(parents=True)
    (assets_root / "screenshots" / "foo.png").write_bytes(b"fake png")
    docs = [_doc("guides/a", "Alpha", "![shot](/screenshots/foo.png)", order=10)]

    forum = FakeForum()
    with _client(forum) as client:  # a real sync — actually uploads
        sync_docs(docs, client=client, category_id=7, assets_root=assets_root)
    assert forum.topics["hal0-docs--guides--a"]["raw"] == "![shot](upload://fake-upload)"

    with _client(forum, dry_run=True) as client:  # same doc, unchanged, planned only
        report = sync_docs(docs, client=client, category_id=7, assets_root=assets_root)

    update_actions = [a for a in report.actions if a.external_id == "hal0-docs--guides--a"]
    assert [a.kind for a in update_actions] == ["noop"]


def test_redirect_map_only_covers_content_docs_not_index_topics() -> None:
    docs = [_doc("guides/a", "Alpha", "Alpha body.", order=10)]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7)

    assert report.redirect_map == {"/docs/guides/a/": report.url_map["guides/a"]}


def test_unresolved_image_falls_back_and_is_warned() -> None:
    docs = [_doc("guides/a", "Alpha", "![shot](/screenshots/missing.png)", order=10)]
    forum = FakeForum()
    with _client(forum) as client:
        report = sync_docs(docs, client=client, category_id=7, assets_root=None)

    assert "https://hal0.dev/screenshots/missing.png" in forum.topics["hal0-docs--guides--a"]["raw"]
    assert any("missing.png" in w for w in report.warnings)
