"""Tests for the vendored hal0-cognee Hermes ``MemoryProvider`` plugin.

Locks the PR-2 contract:

* ABC method surface present + callable.
* Outbound REST payload NEVER carries a ``dataset`` field (issue #317
  client-side regression lock; server-side fix shipped in PR #366).
* ``X-hal0-Agent`` header sourced from ``HAL0_AGENT_ID`` env.
* ``HAL0_MEMORY_BASE`` override is honoured.
* Server 5xx → ``Hal0MemoryClientError`` from the client, ``prefetch``
  + ``sync_turn`` swallow it (best-effort hooks) and downgrade to no-op.

The tests stub ``httpx.AsyncClient`` via ``httpx.MockTransport`` — same
pattern the rest of the hal0 suite uses (see ``tests/lemonade/test_client.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.agents.hermes.plugins.memory_cognee import Hal0CogneeProvider, register
from hal0.agents.hermes.plugins.memory_cognee._client import (
    Hal0MemoryClient,
    Hal0MemoryClientError,
)
from hal0.agents.hermes.plugins.memory_cognee.provider import MemoryProvider

# ── helpers ───────────────────────────────────────────────────────────


class _RequestSpy:
    """Captures every outbound httpx.Request the client emits."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json={"items": [], "status": "ok"})


def _make_provider(
    handler,
    *,
    base_url: str = "http://test",
    agent_id: str = "spy-agent",
) -> Hal0CogneeProvider:
    """Build a provider wired to an httpx.MockTransport-backed client."""
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport, base_url=base_url)
    client = Hal0MemoryClient(
        base_url=base_url,
        agent_id=agent_id,
        http_client=async_client,
    )
    return Hal0CogneeProvider(client=client)


# ── ABC compliance ────────────────────────────────────────────────────


def test_provider_subclasses_memory_provider_abc() -> None:
    provider = Hal0CogneeProvider()
    assert isinstance(provider, MemoryProvider)


def test_provider_implements_all_abstract_methods() -> None:
    provider = Hal0CogneeProvider()
    # name (property) + the four ABC-required abstracts
    assert provider.name == "hal0-cognee"
    assert provider.is_available() is True
    assert provider.get_tool_schemas() == []
    # initialize must accept (session_id, **kwargs)
    provider.initialize("sess-1", agent_context="primary")
    provider.shutdown()


def test_register_collects_provider_via_ctx() -> None:
    class FakeCtx:
        def __init__(self) -> None:
            self.provider: Any = None

        def register_memory_provider(self, provider: Any) -> None:
            self.provider = provider

    ctx = FakeCtx()
    register(ctx)
    assert isinstance(ctx.provider, Hal0CogneeProvider)


# ── identity / headers ────────────────────────────────────────────────


def test_x_hal0_agent_header_set_from_provider_agent_id() -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy, agent_id="hermes-agent-test")
    provider.initialize("sess-x")

    # Drive any REST verb — all paths set the identity header.
    provider.sync_turn("hello", "hi back")

    assert spy.calls, "sync_turn should have emitted at least one request"
    assert spy.calls[0].headers.get("X-hal0-Agent") == "hermes-agent-test"


def test_agent_id_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "from-env")
    monkeypatch.delenv("HAL0_MEMORY_BASE", raising=False)
    client = Hal0MemoryClient()
    try:
        assert client.agent_id == "from-env"
    finally:
        # No live transport; aclose is a no-op against the unused client.
        pass


def test_base_url_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_MEMORY_BASE", "http://hal0.lan:9999/")
    monkeypatch.setenv("HAL0_AGENT_ID", "x")
    client = Hal0MemoryClient()
    # Trailing slash stripped so path joins stay clean.
    assert client.base_url == "http://hal0.lan:9999"


# ── #317 regression lock — payload MUST NOT carry "dataset" ──────────


def test_add_payload_omits_dataset_key() -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy)
    provider.initialize("sess-d")

    provider.sync_turn("u", "a")

    assert spy.calls, "sync_turn should have produced a request"
    request = spy.calls[0]
    assert request.method == "POST"
    assert request.url.path == "/api/memory/add"
    body = _decode_json_body(request)
    assert "dataset" not in body, (
        "client must not send a `dataset` field — server resolves the "
        "private namespace from X-hal0-Agent (issue #317)"
    )
    # Sanity: the text payload we DO send shows up intact so this isn't
    # an accidental no-op route hit.
    assert "User: u" in body.get("text", "")


def test_search_payload_omits_dataset_key() -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy)
    provider.initialize("sess-s")

    provider.prefetch("how do I deploy?")

    assert spy.calls, "prefetch should have produced a request"
    request = spy.calls[0]
    assert request.method == "POST"
    assert request.url.path == "/api/memory/search"
    body = _decode_json_body(request)
    assert "dataset" not in body
    assert body.get("query") == "how do I deploy?"


def test_on_memory_write_mirror_omits_dataset_key() -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy)
    provider.initialize("sess-m")

    provider.on_memory_write("add", "user", "user prefers dark mode")

    assert spy.calls, "on_memory_write should mirror to /api/memory/add"
    body = _decode_json_body(spy.calls[0])
    assert "dataset" not in body
    assert body.get("text") == "user prefers dark mode"


# ── error mapping ─────────────────────────────────────────────────────


def test_client_raises_on_5xx() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "kaboom"})

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://test")
    client = Hal0MemoryClient(
        base_url="http://test",
        agent_id="t",
        http_client=async_client,
    )

    import asyncio

    with pytest.raises(Hal0MemoryClientError) as exc_info:
        asyncio.run(client.add("anything"))
    assert exc_info.value.status_code == 500


def test_prefetch_swallows_transport_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    provider = _make_provider(handler)
    provider.initialize("sess-err")

    # Best-effort hook: a server error must NOT propagate.
    assert provider.prefetch("ignored") == ""


def test_sync_turn_swallows_transport_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = _make_provider(handler)
    provider.initialize("sess-err")

    # Returns None silently; the assertion is "did not raise".
    assert provider.sync_turn("u", "a") is None


# ── write-context gate ────────────────────────────────────────────────


@pytest.mark.parametrize("ctx", ["cron", "flush", "subagent"])
def test_sync_turn_skipped_for_non_primary_contexts(ctx: str) -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy)
    provider.initialize("sess-skip", agent_context=ctx)

    provider.sync_turn("u", "a")

    assert spy.calls == [], f"sync_turn must skip writes in {ctx!r} context"


def test_sync_turn_runs_for_primary_context() -> None:
    spy = _RequestSpy()
    provider = _make_provider(spy)
    provider.initialize("sess-prim", agent_context="primary")

    provider.sync_turn("u", "a")

    assert len(spy.calls) == 1


# ── helpers ───────────────────────────────────────────────────────────


def _decode_json_body(request: httpx.Request) -> dict[str, Any]:
    import json

    raw = request.content
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))
