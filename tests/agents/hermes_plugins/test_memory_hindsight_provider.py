"""Renamed hal0-memory Hermes plugin (P5-H)."""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient
from hal0.agents.hermes.plugins.memory_hindsight.provider import Hal0MemoryProvider


def test_provider_name_is_hal0_memory():
    assert Hal0MemoryProvider().name == "hal0-memory"


def test_no_dataset_field_ever_sent():
    src = inspect.getsource(Hal0MemoryClient.add)
    assert '"dataset"' not in src and "'dataset'" not in src


@pytest.mark.asyncio
async def test_client_recall_hits_recall_route():
    seen: list[tuple[str, str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"items": [{"text": "obs"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as http:
        client = Hal0MemoryClient(http_client=http)
        await client.recall("what do I know", types=["observation", "world"], max_tokens=2048)

    assert seen[0][0] == "POST" and seen[0][1] == "/api/memory/recall"
    assert seen[0][2]["types"] == ["observation", "world"]
    assert "dataset" not in seen[0][2]


def test_prefetch_uses_recall_not_search():
    src = inspect.getsource(Hal0MemoryProvider.prefetch)
    assert ".recall(" in src and ".search(" not in src


def test_writes_stamp_the_author_tag():
    # Convention: tag the author (agent:<id>), bank the scope. Hermes's
    # automatic writes must carry agent:hermes so they're filterable by author
    # without giving each author its own bank.
    for fn in (Hal0MemoryProvider.sync_turn, Hal0MemoryProvider.on_memory_write):
        assert '"agent:hermes"' in inspect.getsource(fn)
    # and the system prompt teaches the convention to model-driven memory_add
    assert "agent:hermes" in inspect.getsource(Hal0MemoryProvider.system_prompt_block)
