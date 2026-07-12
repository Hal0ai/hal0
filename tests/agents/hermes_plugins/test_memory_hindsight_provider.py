"""hal0-memory Hermes plugin — provider behavior (sync transport)."""

from __future__ import annotations

import inspect
import json
import logging

import httpx
import pytest

from hal0.agents.hermes.plugins.memory_hindsight._client import (
    Hal0MemoryClient,
    Hal0MemoryClientError,
)
from hal0.agents.hermes.plugins.memory_hindsight.provider import Hal0MemoryProvider


def _mock_client(handler) -> Hal0MemoryClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://x")
    return Hal0MemoryClient(http_client=http, agent_id="hermes")


def _ok_handler(seen: list) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content or b"{}"),
                dict(request.headers),
            )
        )
        return httpx.Response(200, json={"items": [{"text": "obs"}]})

    return handler


def _down_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def test_provider_name_is_hal0_memory():
    assert Hal0MemoryProvider().name == "hal0-memory"


def test_no_dataset_field_ever_sent():
    src = inspect.getsource(Hal0MemoryClient.add)
    assert '"dataset"' not in src and "'dataset'" not in src


def test_client_recall_hits_recall_route():
    seen: list = []
    client = _mock_client(_ok_handler(seen))
    client.recall("what do I know", types=["observation", "world"], max_tokens=2048)

    assert seen[0][0] == "POST" and seen[0][1] == "/api/memory/recall"
    assert seen[0][2]["types"] == ["observation", "world"]
    assert "dataset" not in seen[0][2]


def test_add_shared_flag_flips_private_header():
    seen: list = []
    client = _mock_client(_ok_handler(seen))
    client.add("fact", private=True)
    client.add("fact", private=False)
    assert seen[0][3]["x-hal0-private"] == "1"
    assert seen[1][3]["x-hal0-private"] == "0"


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


def test_system_prompt_two_banks_for_plain_agent_id():
    provider = Hal0MemoryProvider(client=_mock_client(_ok_handler([])))
    provider.initialize("s1")
    block = provider.system_prompt_block()
    assert "private:hermes" in block and "SHARED" in block
    assert "THREE tiers" not in block


def test_system_prompt_three_tiers_for_profile_agent_id():
    seen: list = []
    transport = httpx.MockTransport(_ok_handler(seen))
    http = httpx.Client(transport=transport, base_url="http://x")
    client = Hal0MemoryClient(http_client=http, agent_id="hermes__research")
    provider = Hal0MemoryProvider(client=client)
    provider.initialize("s1")
    block = provider.system_prompt_block()
    assert "THREE tiers" in block
    assert "private:hermes__research" in block
    assert "private:hermes**" in block or "private:hermes " in block or "private:hermes)" in block
    assert "shared" in block.lower()


def test_initialize_probe_sets_degraded_and_prompt_note(caplog):
    provider = Hal0MemoryProvider(client=_mock_client(_down_handler))
    with caplog.at_level(logging.WARNING):
        provider.initialize("s1")
    assert provider._degraded is True
    assert "UNREACHABLE" in provider.system_prompt_block()
    assert any("unreachable" in r.message.lower() for r in caplog.records)


def test_first_failure_warns_then_debug(caplog):
    provider = Hal0MemoryProvider(client=_mock_client(_down_handler))
    provider.initialize("s1")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        provider.sync_turn("hi", "yo")
        provider.sync_turn("hi2", "yo2")
    records = [r for r in caplog.records if "sync_turn" in r.message]
    assert records[0].levelno == logging.WARNING
    assert records[1].levelno == logging.DEBUG
    assert provider.failure_counts == {"sync_turn": 2}


def test_success_clears_degraded():
    seen: list = []
    ok = _ok_handler(seen)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:  # probe + first sync_turn fail
            raise httpx.ConnectError("refused", request=request)
        return ok(request)

    provider = Hal0MemoryProvider(client=_mock_client(flaky))
    provider.initialize("s1")
    provider.sync_turn("a", "b")
    assert provider._degraded is True
    provider.sync_turn("a", "b")
    assert provider._degraded is False
    assert "UNREACHABLE" not in provider.system_prompt_block()


def test_skip_write_contexts_drop_sync_turn():
    seen: list = []
    provider = Hal0MemoryProvider(client=_mock_client(_ok_handler(seen)))
    provider.initialize("s1", agent_context="cron")
    provider.sync_turn("user", "assistant")
    # only the initialize() probe hit the wire
    assert all(path == "/api/memory/list" for _, path, _, _ in seen)


def test_tool_add_shared_reports_bank():
    seen: list = []
    provider = Hal0MemoryProvider(client=_mock_client(_ok_handler(seen)))
    provider.initialize("s1")
    out = json.loads(provider.handle_tool_call("hal0_memory_add", {"text": "f", "shared": True}))
    assert out.get("bank") == "shared"
    add_calls = [(m, p, h) for m, p, _, h in seen if p == "/api/memory/add"]
    assert add_calls[0][2]["x-hal0-private"] == "0"


def test_tool_error_is_json_not_raise():
    provider = Hal0MemoryProvider(client=_mock_client(_down_handler))
    provider.initialize("s1")
    out = json.loads(provider.handle_tool_call("hal0_memory_search", {"query": "q"}))
    assert out["status"] == "error"
    assert provider.failure_counts.get("tool:hal0_memory_search") == 1


def test_client_error_carries_status_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = _mock_client(handler)
    with pytest.raises(Hal0MemoryClientError) as exc_info:
        client.search("q")
    assert exc_info.value.status_code == 503
