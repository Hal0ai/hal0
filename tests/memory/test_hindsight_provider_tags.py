"""The provider owns ``agent:`` and ``visibility:`` tags on every write (#3136)."""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient


def _provider() -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), unified_bank=True)


@pytest.mark.asyncio
async def test_add_overwrites_caller_agent_tag():
    p = _provider()
    await p.add("x", tags=["agent:brain", "visibility:private"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert "agent:openwebui" in tags
    assert "agent:brain" not in tags
    assert "visibility:private" not in tags


@pytest.mark.asyncio
async def test_add_keeps_private_when_requested():
    p = _provider()
    await p.add("x", dataset="private:openwebui", tags=["agent:brain"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert tags.count("visibility:private") == 1
    assert "agent:openwebui" in tags
    assert "agent:brain" not in tags


@pytest.mark.asyncio
async def test_add_anonymous_gets_unknown_agent():
    p = _provider()
    await p.add("x", tags=["agent:brain"], client_id=None)
    tags = p._client.retained[-1]["tags"]
    assert "agent:brain" not in tags
    assert tags.count("agent:unknown") == 1


@pytest.mark.asyncio
async def test_add_keeps_unrelated_and_project_tags():
    p = _provider()
    await p.add(
        "x",
        dataset="project:hal0",
        tags=["topic:cats", "user:alexander", "agent:other"],
        client_id="openwebui",
    )
    tags = p._client.retained[-1]["tags"]
    assert {"topic:cats", "user:alexander", "project:hal0", "agent:openwebui"} <= set(tags)
    assert [t for t in tags if t.startswith("agent:")] == ["agent:openwebui"]
