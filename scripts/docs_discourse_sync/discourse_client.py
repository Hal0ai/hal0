"""Discourse admin API client for the docs sync.

Endpoints mirror discourse/discourse-developer-docs' own ``sync_docs``
script (``lib/api.rb``): ``POST /posts.json`` to create a topic with an
``external_id``, ``PUT /posts/{id}.json`` to edit its first post, and
``POST /uploads.json`` (multipart) for image uploads. This project's
own param shapes were verified against Discourse's actual
``PostsController`` source rather than assumed from upstream by
analogy — ``create``'s ``category`` is a top-level param,
``update``'s category change has to be ``post.category_id`` (a
different field, nested), and ``skip_validations`` only bypasses topic
validations (title length among them) on create, not on this update
endpoint — see :meth:`DiscourseClient.update_topic`'s docstring. Where
this diverges from upstream more deliberately is topic lookup: upstream
resolves every doc's topic in one batch via a Data Explorer query
(``fetch_current_state``); this project's own task spec calls for the
plain, documented per-doc endpoint instead — ``GET
/t/external_id/{id}.json`` — which needs no Data Explorer plugin
installed and is simpler to reason about at hal0's ~50-doc scale.

Every mutating call is skipped under ``dry_run`` and returns a synthesized
placeholder instead, so downstream planning (link rewrite pass 2, index
topics) can exercise its full logic against a consistent shape without
ever reaching the network — this is also exactly why tests never need
live credentials: they swap in an ``httpx.MockTransport``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_TIMEOUT_S = 20.0


class DiscourseAPIError(RuntimeError):
    """A Discourse admin API call returned a status this client doesn't
    otherwise handle (see :meth:`DiscourseClient.resolve_topic` for the
    one intentional not-an-error case: 404)."""

    def __init__(self, method: str, path: str, response: httpx.Response) -> None:
        self.method = method
        self.path = path
        self.status_code = response.status_code
        self.body = response.text
        super().__init__(f"{method} {path} -> {response.status_code}: {response.text[:500]}")


@dataclass(slots=True)
class Topic:
    topic_id: int
    first_post_id: int
    slug: str
    title: str
    raw: str
    category_id: int

    def url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/t/{self.slug}/{self.topic_id}"


class RateLimiter:
    """Sliding min-interval throttle: never issue two requests less than
    ``60 / requests_per_minute`` seconds apart. Simple and sufficient at
    this tool's volume (dozens of calls per run, no bursts) — a token
    bucket buys nothing extra here. ``clock``/``sleeper`` are injectable so
    tests can assert throttling happens without a real sleep.
    """

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._min_interval = 60.0 / requests_per_minute
        self._clock = clock
        self._sleeper = sleeper
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self._min_interval:
                self._sleeper(self._min_interval - elapsed)
                now = self._clock()
        self._last = now


class DiscourseClient:
    """Thin sync wrapper over ``httpx.Client``. Pass ``transport=`` (an
    ``httpx.MockTransport``) in tests to avoid any real network access."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_username: str,
        dry_run: bool = False,
        requests_per_minute: int = 60,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._rate_limiter = rate_limiter or RateLimiter(requests_per_minute)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=_DEFAULT_TIMEOUT_S,
            transport=transport,
            headers={
                "Api-Key": api_key,
                "Api-Username": api_username,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DiscourseClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._rate_limiter.wait()
        return self._client.request(method, path, **kwargs)

    def resolve_topic(self, external_id: str) -> Topic | None:
        """``GET /t/external_id/{id}.json?include_raw=true``.

        Read-only — always runs, even under ``dry_run``, so planning can
        tell create from update (and diff-report what would change)
        without ever calling a mutating endpoint. Returns ``None`` on 404
        (no topic with this external_id yet), which is the expected,
        common case on a doc's first sync — not an error.
        """
        path = f"/t/external_id/{external_id}.json"
        response = self._request("GET", path, params={"include_raw": "true"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DiscourseAPIError("GET", path, response)
        data = response.json()
        posts = data.get("post_stream", {}).get("posts", [])
        first_post_id = posts[0]["id"] if posts else data["id"]
        raw = posts[0].get("raw", "") if posts else ""
        return Topic(
            topic_id=data["id"],
            first_post_id=first_post_id,
            slug=data.get("slug", "-"),
            title=data.get("title", ""),
            raw=raw,
            category_id=data.get("category_id", -1),
        )

    def create_topic(self, *, external_id: str, title: str, raw: str, category_id: int) -> Topic:
        """``POST /posts.json`` — sets ``external_id`` at creation, same as
        upstream's ``API.create_topic``. Skipped under dry_run; returns a
        placeholder so pass-2 planning still has a URL shape to reason
        about."""
        if self.dry_run:
            return Topic(
                topic_id=-1,
                first_post_id=-1,
                slug="dry-run",
                title=title,
                raw=raw,
                category_id=category_id,
            )
        response = self._request(
            "POST",
            "/posts.json",
            json={
                "title": title,
                "raw": raw,
                "category": category_id,
                "external_id": external_id,
                # Verified against Discourse's PostsController#create_params:
                # `is_api?` (true for an Api-Key/Api-Username request, which is
                # all this client ever makes) permits `skip_validations`, and
                # PostCreator skips TopicCreator's validate_child entirely when
                # it's set — including topic_title_length. Without it, any doc
                # whose title is under the site's min_topic_title_length
                # (Discourse's own out-of-box default is 15; hal0's docs/
                # corpus has 14 titles under that, e.g. "Slots", "Memory")
                # 422s on its very first sync.
                "skip_validations": True,
            },
        )
        if response.status_code not in (200, 201):
            raise DiscourseAPIError("POST", "/posts.json", response)
        data = response.json()
        return Topic(
            topic_id=data["topic_id"],
            first_post_id=data["id"],
            slug=data.get("topic_slug", "-"),
            title=title,
            raw=raw,
            # Not parsed from the response: we already know it, we just
            # created the topic with it.
            category_id=category_id,
        )

    def update_topic(self, *, post_id: int, title: str, raw: str, category_id: int) -> None:
        """``PUT /posts/{id}.json``. Skipped under dry_run.

        ``title`` is a top-level param (``PostsController#update`` reads
        ``params[:title]`` directly) but the category change has to be
        ``post.category_id``, not a top-level ``category`` — that field
        doesn't exist on this endpoint at all and was silently ignored
        (verified against ``PostsController#update``, which reads
        ``params[:post][:category_id]``). A category-drift correction
        used to report success while leaving the topic in its old
        category, so the next sync would detect "changed" and try again,
        forever.

        ``skip_validations`` is included for parity with create, but
        verified NOT to reliably bypass Discourse's title-length check
        here the way it does on create: ``PostsController#update`` never
        wires a client-supplied ``skip_validations`` through to
        ``PostRevisor`` (only auto-sets it for staff-edited small_action
        posts), and ``Topic``'s title validator re-runs whenever
        ``category_id_changed?`` — not just when the title itself
        changes — so a short-titled doc *would* still 422 on exactly the
        category-correction update this method exists to make. See
        ``discovery.py``'s title padding for the actual fix to that gap.
        """
        if self.dry_run:
            return
        path = f"/posts/{post_id}.json"
        response = self._request(
            "PUT",
            path,
            json={
                "post": {
                    "raw": raw,
                    "edit_reason": "Synced from Hal0ai/hal0 docs/",
                    "category_id": category_id,
                    "skip_validations": True,
                },
                "title": title,
            },
        )
        if response.status_code != 200:
            raise DiscourseAPIError("PUT", path, response)

    def upload(self, path: Path) -> str:
        """``POST /uploads.json`` (multipart, ``type=composer``,
        ``synchronous=true``) -> the resulting ``upload://`` short-URL.
        Skipped under dry_run."""
        if self.dry_run:
            return f"upload://dry-run-{path.name}"
        with path.open("rb") as fh:
            response = self._request(
                "POST",
                "/uploads.json",
                data={"type": "composer", "synchronous": "true"},
                files={"file": (path.name, fh)},
            )
        if response.status_code != 200:
            raise DiscourseAPIError("POST", "/uploads.json", response)
        short_url = response.json().get("short_url")
        if not short_url:
            raise DiscourseAPIError("POST", "/uploads.json", response)
        return short_url
