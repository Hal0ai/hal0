"""``MemoryDispatcher.scoped`` and ``.shared``: in-process callers with a fixed identity.

The app-level dispatcher resolves the caller and the private-mode toggle off the
live MCP request, and the toggle is an ``X-hal0-Private`` header the caller
controls. An in-process caller (the brain steward, the agent bridge) has no such
request and must not trust one: ``scoped`` always writes ``private:<client_id>``,
``shared`` always writes ``shared`` (ADR-0043 decision 10), whatever the app-level
resolvers say.
"""

from __future__ import annotations

from typing import Any

from hal0.dispatcher.memory_dispatcher import MemoryDispatcher


class _Provider:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []
        self.searched: list[dict[str, Any]] = []

    async def add(self, **kw: Any) -> dict[str, Any]:
        self.added.append(kw)
        return {"id": "doc-1", "timestamp": "2026-10-29T00:00:00Z"}

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        self.searched.append(kw)
        return []


def _app_level(provider: _Provider, *, preflight: Any = None) -> MemoryDispatcher:
    # A request from someone else who asked for private mode.
    return MemoryDispatcher(
        provider,
        client_id_resolver=lambda: "mallory",
        private_resolver=lambda: True,
        add_preflight=preflight,
    )


async def test_scoped_forces_private_mode_for_its_client() -> None:
    provider = _Provider()
    scoped = MemoryDispatcher(provider).scoped("hermes__coder")
    out = await scoped.dispatch("memory_add", {"text": "t", "dataset": "shared"})
    assert out["status"] == "ok"
    [added] = provider.added
    assert added["dataset"] == "private:hermes__coder" and added["source"] == "hermes__coder"


async def test_shared_never_promotes_to_private_whatever_the_request_says() -> None:
    provider = _Provider()
    shared = _app_level(provider).shared("cc")
    await shared.dispatch("memory_add", {"text": "t", "dataset": "shared", "tags": ["a"]})
    await shared.dispatch("memory_search", {"query": "q", "dataset": "shared"})
    [added] = provider.added
    assert added["dataset"] == "shared" and added["client_id"] == "cc" and added["source"] == "cc"
    [searched] = provider.searched
    assert searched["dataset"] == "shared" and searched["client_id"] == "cc"


async def test_shared_cannot_address_a_private_namespace() -> None:
    provider = _Provider()
    out = (
        await _app_level(provider)
        .shared("cc")
        .dispatch("memory_add", {"text": "t", "dataset": "private:hermes"})
    )
    assert out["status"] == "error" and out["error"]["code"] == "mcp.memory_schema"
    assert provider.added == []


async def test_both_keep_the_add_preflight_of_the_app_level_dispatcher() -> None:
    ran: list[str] = []

    async def preflight() -> None:
        ran.append("ran")

    provider = _Provider()
    base = _app_level(provider, preflight=preflight)
    await base.scoped("hermes").dispatch("memory_add", {"text": "t"})
    await base.shared("cc").dispatch("memory_add", {"text": "t"})
    assert ran == ["ran", "ran"]
