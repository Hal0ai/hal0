"""Tests for scripts/docs_discourse_sync/discourse_client.py.

No live HTTP: every test wires an ``httpx.MockTransport`` in. This is
also the "how the pilot proves out with zero credentials" seam the task
asked for — a real run swaps the transport for nothing (it's httpx's
default), everything else is unchanged.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from scripts.docs_discourse_sync.discourse_client import (
    DiscourseAPIError,
    DiscourseClient,
    RateLimiter,
    Topic,
)


def _client(handler, **kwargs) -> DiscourseClient:
    return DiscourseClient(
        base_url="https://forum.hal0.dev",
        api_key="test-key",
        api_username="system",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _resolve_topic_json() -> dict:
    return {
        "id": 55,
        "slug": "install-hal0",
        "title": "Install hal0",
        "category_id": 7,
        "post_stream": {"posts": [{"id": 999, "raw": "content here"}]},
    }


def test_resolve_topic_found() -> None:
    """Discourse's real /t/external_id/{id}.json NEVER returns the topic
    JSON directly — TopicsController#show_by_external_id always
    redirect_to_correct_topic (301 to the canonical /t/<slug>/<id>.json,
    verified against the controller source). Every mock here follows
    that same two-hop shape so the test suite doesn't diverge from the
    live pilot's actual behavior the way it did before this was found."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/t/external_id/hal0-docs--getting-started--install.json":
            assert request.url.params["include_raw"] == "true"
            return httpx.Response(
                301,
                headers={"Location": "/t/install-hal0/55.json?include_raw=true"},
            )
        if request.url.path == "/t/install-hal0/55.json":
            assert request.url.params["include_raw"] == "true"
            return httpx.Response(200, json=_resolve_topic_json())
        raise AssertionError(f"unexpected request: {request.url.path}")

    with _client(handler) as client:
        topic = client.resolve_topic("hal0-docs--getting-started--install")

    assert topic == Topic(
        topic_id=55,
        first_post_id=999,
        slug="install-hal0",
        title="Install hal0",
        raw="content here",
        category_id=7,
    )
    assert topic.url("https://forum.hal0.dev") == "https://forum.hal0.dev/t/install-hal0/55"


def test_create_topic_does_not_follow_a_redirect() -> None:
    """Redirect-following is scoped to resolve_topic's GET, not enabled
    client-wide — a redirected mutating call changing method or
    replaying a body is a very different, riskier thing to do silently.
    A 301 from a mutating call should surface as an error, not get
    silently followed and treated as success."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "/posts/999.json"})

    with _client(handler) as client, pytest.raises(DiscourseAPIError) as exc_info:
        client.create_topic(external_id="hal0-docs--x", title="X", raw="raw", category_id=7)
    assert exc_info.value.status_code == 301


def test_resolve_topic_lookup_path_has_exactly_one_id_segment() -> None:
    """Regression: external_id values used to be '/'-joined
    (e.g. "hal0-docs/reference/api/rest-api"), which turned
    /t/external_id/{id}.json into multiple path segments — Discourse's
    own route constraint (`external_id: /[\\w-]+/` in config/routes.rb)
    only matches one. external_id is slash-free now (see discovery.py's
    make_external_id), so the lookup path must resolve to exactly one
    segment after "external_id/"."""
    seen_path = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_path["path"] = request.url.path
        return httpx.Response(404, json={"error_type": "not_found"})

    with _client(handler) as client:
        client.resolve_topic("hal0-docs--reference--api--rest-api")

    id_segment = seen_path["path"].removeprefix("/t/external_id/").removesuffix(".json")
    assert "/" not in id_segment
    assert id_segment == "hal0-docs--reference--api--rest-api"


def test_resolve_topic_not_found_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error_type": "not_found"})

    with _client(handler) as client:
        assert client.resolve_topic("hal0-docs/missing") is None


def test_resolve_topic_server_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _client(handler) as client, pytest.raises(DiscourseAPIError) as exc_info:
        client.resolve_topic("hal0-docs/x")
    assert exc_info.value.status_code == 500


def test_create_topic_posts_expected_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 12, "topic_id": 34, "topic_slug": "install-hal0"})

    with _client(handler) as client:
        topic = client.create_topic(
            external_id="hal0-docs--getting-started--install",
            title="Install hal0",
            raw="body md",
            category_id=7,
        )

    assert captured["path"] == "/posts.json"
    assert captured["body"] == {
        "title": "Install hal0",
        "raw": "body md",
        "category": 7,
        "external_id": "hal0-docs--getting-started--install",
        "skip_validations": True,
    }
    assert topic == Topic(
        topic_id=34,
        first_post_id=12,
        slug="install-hal0",
        title="Install hal0",
        raw="body md",
        category_id=7,
    )


def test_create_topic_dry_run_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry-run must not call the network for a mutating request")

    with _client(handler, dry_run=True) as client:
        topic = client.create_topic(external_id="hal0-docs--x", title="X", raw="raw", category_id=7)
    assert topic.topic_id == -1
    assert topic.category_id == 7


def test_update_topic_puts_expected_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    with _client(handler) as client:
        client.update_topic(post_id=999, title="Install hal0", raw="new body", category_id=7)

    assert captured["path"] == "/posts/999.json"
    assert captured["body"]["post"]["raw"] == "new body"
    assert captured["body"]["title"] == "Install hal0"
    # category_id belongs nested under "post" (verified against
    # PostsController#update, which reads params[:post][:category_id]) —
    # a top-level "category" (create's field name) is silently ignored here.
    assert "category" not in captured["body"]
    assert captured["body"]["post"]["category_id"] == 7
    assert captured["body"]["post"]["skip_validations"] is True


def test_update_topic_dry_run_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry-run must not call the network for a mutating request")

    with _client(handler, dry_run=True) as client:
        client.update_topic(post_id=1, title="X", raw="raw", category_id=7)


def test_upload_returns_short_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/uploads.json"
        return httpx.Response(200, json={"short_url": "upload://abc123"})

    with _client(handler) as client, tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(b"fake")
        f.flush()
        short_url = client.upload(Path(f.name))
    assert short_url == "upload://abc123"


def test_upload_dry_run_makes_no_request(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry-run must not call the network for a mutating request")

    path = tmp_path / "foo.png"
    path.write_bytes(b"fake")
    with _client(handler, dry_run=True) as client:
        short_url = client.upload(path)
    assert short_url.startswith("upload://dry-run-")


def test_error_response_raises_with_status_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text='{"errors": ["bad"]}')

    with _client(handler) as client, pytest.raises(DiscourseAPIError) as exc_info:
        client.create_topic(external_id="x", title="X", raw="r", category_id=1)
    assert exc_info.value.status_code == 422
    assert "bad" in exc_info.value.body


class _FakeClock:
    """A controllable clock whose ``sleeper`` counterpart advances it by
    the requested duration — so "time passes" only when the test says it
    does, and ``wait()``'s second ``clock()`` read after sleeping (used to
    record the *actual* last-request time) reflects that."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_rate_limiter_sleeps_to_maintain_min_interval() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    limiter = RateLimiter(60, clock=clock, sleeper=sleeper)  # min_interval = 1.0s

    limiter.wait()  # t=0.0 — no prior request, no sleep
    clock.advance(0.1)  # only 0.1s of "work" happens before the next request
    limiter.wait()  # t=0.1 — 0.1s since last (<1.0s), sleeps 0.9s to catch up
    clock.advance(5.0)  # then a long gap before the next request
    limiter.wait()  # t=6.0 — 5.0s since last (>1.0s), no sleep needed

    assert sleeps == [pytest.approx(0.9)]


def test_rate_limiter_rejects_non_positive_rpm() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# --- 429 backpressure -------------------------------------------------------
#
# Discourse throttles per-user, per-IP and per-API-key on buckets the client's
# own RateLimiter knows nothing about, so a 429 arrives mid-run as a matter of
# course. It used to abort the whole sync: the sync-docs-discourse workflow
# failed exactly this way from 2026-08-22 onward, on a topic lookup.


def _resolving_handler(responses: list[httpx.Response], calls: dict[str, int]):
    """Handler that answers the external_id lookup from ``responses`` in
    order (the last one repeats), then serves the canonical topic hop."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/t/external_id/hal0-docs--getting-started--install.json":
            calls["n"] += 1
            return responses[min(calls["n"] - 1, len(responses) - 1)]
        if request.url.path == "/t/install-hal0/55.json":
            return httpx.Response(200, json=_resolve_topic_json())
        raise AssertionError(f"unexpected request: {request.url.path}")

    return handler


_REDIRECT = httpx.Response(301, headers={"Location": "/t/install-hal0/55.json?include_raw=true"})


def test_rate_limited_request_is_retried_after_the_wait_the_server_asks_for() -> None:
    slept: list[float] = []
    calls = {"n": 0}
    throttled = httpx.Response(
        429,
        json={
            "errors": ["You've performed this action too many times."],
            "error_type": "rate_limit",
            "extras": {"wait_seconds": 10},
        },
    )

    with _client(_resolving_handler([throttled, _REDIRECT], calls), sleeper=slept.append) as client:
        topic = client.resolve_topic("hal0-docs--getting-started--install")

    assert topic is not None
    assert calls["n"] == 2, "the throttled lookup is retried once"
    assert slept == [11.0], "waits the server's wait_seconds plus a second of slack"


def test_rate_limit_retry_falls_back_to_the_retry_after_header() -> None:
    slept: list[float] = []
    calls = {"n": 0}
    throttled = httpx.Response(429, headers={"Retry-After": "4"}, text="slow down")

    with _client(_resolving_handler([throttled, _REDIRECT], calls), sleeper=slept.append) as client:
        assert client.resolve_topic("hal0-docs--getting-started--install") is not None

    assert slept == [5.0]


def test_rate_limit_retries_are_bounded_and_then_the_error_surfaces() -> None:
    """A forum that never lets up must still fail the run, not spin forever."""
    slept: list[float] = []
    calls = {"n": 0}
    throttled = httpx.Response(429, json={"extras": {"wait_seconds": 1}})

    with (
        _client(
            _resolving_handler([throttled], calls),
            sleeper=slept.append,
            max_rate_limit_retries=2,
        ) as client,
        pytest.raises(DiscourseAPIError) as excinfo,
    ):
        client.resolve_topic("hal0-docs--getting-started--install")

    assert calls["n"] == 3, "the first attempt plus max_rate_limit_retries"
    assert len(slept) == 2, "no sleep after the final attempt"
    assert "429" in str(excinfo.value)


def test_server_errors_are_not_retried() -> None:
    """Only 429 is backpressure; a 500 is a broken forum and must surface."""
    slept: list[float] = []
    calls = {"n": 0}

    with (
        _client(
            _resolving_handler([httpx.Response(500, text="boom")], calls), sleeper=slept.append
        ) as client,
        pytest.raises(DiscourseAPIError),
    ):
        client.resolve_topic("hal0-docs--getting-started--install")

    assert calls["n"] == 1
    assert slept == []
