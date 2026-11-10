"""``POST /api/memory/add`` carries ``update_mode`` and ``observation_scopes``
to the provider, and from there to the engine (#2017, ADR-0058 decision 5).

The fact this file exists for: Hindsight 1.2.0 REPLACES a document when a
retain reuses its ``document_id``, unless the retain item says
``update_mode: "append"`` (engine ``api/http.py``, ``MemoryItem.update_mode``:
"'replace' (default) deletes old data and reprocesses from scratch. 'append'
concatenates new content to the existing document text and reprocesses").
The Hermes plugin sends one id for a whole session, so every such write must
reach the engine marked ``append``. The last tests here run the real plugin
against the real route and the real provider with only the engine mocked, and
assert the mode on the engine call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes.plugins.memory_hindsight import Hal0MemoryProvider
from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient
from hal0.agents.hermes_provision import profile_agent_id
from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.memory.hindsight_provider import HindsightProvider


def _app(provider: Any) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    return app


class RecordingWrapper:
    """Stands in for the provider and records exactly the keywords it got."""

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        self.add_calls.append(kwargs)
        return {"id": kwargs.get("document_id") or "fresh", "timestamp": "t"}


@pytest.fixture
def wrapper() -> RecordingWrapper:
    return RecordingWrapper()


@pytest.fixture
def http(wrapper: RecordingWrapper) -> Iterator[TestClient]:
    with TestClient(_app(wrapper)) as client:
        yield client


# ── the route ──────────────────────────────────────────────────────────────


def test_add_forwards_update_mode_and_echoes_it(
    http: TestClient, wrapper: RecordingWrapper
) -> None:
    r = http.post(
        "/api/memory/add",
        json={"text": "turn 2", "document_id": "hermes-s1", "update_mode": "append"},
        headers={"X-hal0-Agent": "hermes", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    (call,) = wrapper.add_calls
    assert call["document_id"] == "hermes-s1"
    assert call["update_mode"] == "append"
    # The acknowledgement an appending caller checks before reusing the id.
    assert r.json()["update_mode"] == "append"


def test_add_forwards_observation_scopes_as_sent(
    http: TestClient, wrapper: RecordingWrapper
) -> None:
    for scopes in ("shared", [["topic:*", "user:*"]]):
        r = http.post("/api/memory/add", json={"text": "x", "observation_scopes": scopes})
        assert r.status_code == 200, r.text
        assert wrapper.add_calls[-1]["observation_scopes"] == scopes


def test_add_sets_no_default_for_either_field(http: TestClient, wrapper: RecordingWrapper) -> None:
    """The provider owns the scoping default and the engine the mode: a write
    that names neither must reach the provider with neither."""
    r = http.post("/api/memory/add", json={"text": "x", "document_id": "notes"})
    assert r.status_code == 200, r.text
    (call,) = wrapper.add_calls
    assert "update_mode" not in call and "observation_scopes" not in call
    assert "update_mode" not in r.json()


@pytest.mark.parametrize(
    "body",
    [
        {"text": "x", "document_id": "d", "update_mode": "merge"},
        {"text": "x", "document_id": "d", "update_mode": True},
        # The engine refuses an append with nothing to append to.
        {"text": "x", "update_mode": "append"},
        {"text": "x", "observation_scopes": 7},
        {"text": "x", "observation_scopes": {"scope": "shared"}},
    ],
)
def test_add_rejects_malformed_fields(
    http: TestClient, wrapper: RecordingWrapper, body: dict[str, Any]
) -> None:
    r = http.post("/api/memory/add", json=body)
    assert r.status_code == 400, r.text
    assert wrapper.add_calls == []


# ── plugin → route → provider → engine ─────────────────────────────────────


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.retain.return_value = {"operation_id": "op-1"}
    return engine


def _plugin(http: TestClient, *, agent_id: str, session_id: str) -> Hal0MemoryProvider:
    provider = Hal0MemoryProvider(client=Hal0MemoryClient(http_client=http, agent_id=agent_id))
    provider.initialize(session_id=session_id)
    return provider


@pytest.mark.parametrize("unified_bank", [True, False])
def test_session_writes_reach_the_engine_in_append_mode(unified_bank: bool) -> None:
    """Three turns and a checkpoint of one session: four engine retains, all on
    one document id, every one of them ``update_mode="append"``. A single
    retain without the mode would delete the turns before it."""
    engine = _engine()
    provider = HindsightProvider(client=engine, unified_bank=unified_bank)
    with TestClient(_app(provider)) as http:
        plugin = _plugin(http, agent_id="hermes", session_id="20261110_101500_ab12cd")
        for n in range(3):
            plugin.sync_turn(f"question {n}", f"answer {n}")
        plugin.on_session_end([{"role": "user", "content": "bye"}])

    retains = [call.kwargs for call in engine.retain.await_args_list]
    assert len(retains) == 4
    assert {r["update_mode"] for r in retains} == {"append"}
    # One document. In the unified bank a private id carries the owner prefix
    # (``HindsightProvider.add``); a private bank needs none.
    expected = "hermes-20261110_101500_ab12cd"
    if unified_bank:
        expected = f"private:hermes:{expected}"
    assert {r["document_id"] for r in retains} == {expected}
    # One tag set across all four, so the engine's re-stamp changes nothing.
    assert len({tuple(sorted(r["tags"])) for r in retains}) == 1
    assert "visibility:private" in retains[0]["tags"]
    assert retains[0]["content"] == "User: question 0\nAssistant: answer 0"
    # The plugin saw the route's acknowledgement and kept the session id.
    assert plugin._session_documents is True


def test_a_named_profile_gets_its_own_session_document() -> None:
    engine = _engine()
    provider = HindsightProvider(client=engine, unified_bank=True)
    agent_id = profile_agent_id("work")
    with TestClient(_app(provider)) as http:
        plugin = _plugin(http, agent_id=agent_id, session_id="s1")
        plugin.sync_turn("q", "a")
    (retain,) = [call.kwargs for call in engine.retain.await_args_list]
    assert retain["document_id"] == f"private:{agent_id}:hermes-work-s1"
    assert retain["update_mode"] == "append"


def test_an_explicit_retain_reaches_the_engine_with_a_fresh_id_and_no_mode() -> None:
    engine = _engine()
    provider = HindsightProvider(client=engine, unified_bank=True)
    with TestClient(_app(provider)) as http:
        plugin = _plugin(http, agent_id="hermes", session_id="s1")
        plugin.handle_tool_call("hindsight_retain", {"text": "a durable fact"})
        plugin.handle_tool_call("hindsight_retain", {"text": "another"})
    first, second = [call.kwargs for call in engine.retain.await_args_list]
    assert first["document_id"] != second["document_id"]
    assert "hermes-s1" not in first["document_id"]
    assert "update_mode" not in first and "update_mode" not in second
