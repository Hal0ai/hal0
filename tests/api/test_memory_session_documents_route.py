"""``POST /api/memory/add``: appending to a document, and what reaches the
engine (#2017, ADR-0058 decision 5).

The fact this file exists for: Hindsight 1.2.0 REPLACES a document when a
retain reuses its ``document_id``, unless the retain item says
``update_mode: "append"`` (engine ``api/http.py``, ``MemoryItem.update_mode``:
"'replace' (default) deletes old data and reprocesses from scratch. 'append'
concatenates new content to the existing document text and reprocesses").

The route offers the append two ways. ``document_id`` with ``update_mode``
is the engine's own pair. ``append_document_id`` is the same thing as one
field, for the Hermes plugin: a hal0-api from before this change reads
``document_id``, drops ``update_mode`` and replaces, but ignores a field it
does not know. The response carries ``update_mode`` only when the provider
reports it sent the mode to the engine.

The last tests run the real plugin against the real route and the real
provider with only the engine mocked, and assert the mode on the engine call.
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
from hal0.memory.pgvector_provider import PgVectorProvider

PRIVATE = {"X-hal0-Agent": "hermes", "X-hal0-Private": "1"}


def _app(provider: Any) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    return app


class RecordingWrapper:
    """Stands in for the provider and records exactly the keywords it got.
    Like a provider that does nothing with ``update_mode``, it reports none."""

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


def _engine() -> AsyncMock:
    engine = AsyncMock()
    engine.retain.return_value = {"operation_id": "op-1"}
    return engine


# ── append_document_id ─────────────────────────────────────────────────────


def test_append_document_id_is_document_id_plus_append(
    http: TestClient, wrapper: RecordingWrapper
) -> None:
    r = http.post(
        "/api/memory/add",
        json={"text": "turn 2", "append_document_id": "hermes-s1"},
        headers=PRIVATE,
    )
    assert r.status_code == 200, r.text
    (call,) = wrapper.add_calls
    assert call["document_id"] == "hermes-s1"
    assert call["update_mode"] == "append"


@pytest.mark.parametrize(
    "extra",
    [
        {"document_id": "hermes-s1"},
        {"document_id": "another"},
        {"update_mode": "append"},
        {"update_mode": "replace"},
        {"document_id": "hermes-s1", "update_mode": "append"},
    ],
)
def test_append_document_id_has_exactly_one_meaning(
    http: TestClient, wrapper: RecordingWrapper, extra: dict[str, str]
) -> None:
    r = http.post(
        "/api/memory/add",
        json={"text": "x", "append_document_id": "hermes-s1", **extra},
        headers=PRIVATE,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "memory.append_document_id_conflict"
    assert wrapper.add_calls == []


@pytest.mark.parametrize("value", ["../escape", "", 7, "x" * 65, ["hermes-s1"]])
def test_append_document_id_follows_the_document_id_grammar(
    http: TestClient, wrapper: RecordingWrapper, value: Any
) -> None:
    r = http.post("/api/memory/add", json={"text": "x", "append_document_id": value})
    assert r.status_code == 400, r.text
    assert wrapper.add_calls == []


def test_a_private_append_document_id_needs_a_private_write(
    http: TestClient, wrapper: RecordingWrapper
) -> None:
    body = {"text": "x", "append_document_id": "private:hermes:hermes-s1"}
    assert http.post("/api/memory/add", json=body).status_code == 400
    assert http.post("/api/memory/add", json=body, headers=PRIVATE).status_code == 200
    assert wrapper.add_calls[0]["document_id"] == "private:hermes:hermes-s1"


# ── update_mode and observation_scopes pass through ────────────────────────


def test_add_forwards_update_mode(http: TestClient, wrapper: RecordingWrapper) -> None:
    r = http.post(
        "/api/memory/add",
        json={"text": "turn 2", "document_id": "notes", "update_mode": "append"},
    )
    assert r.status_code == 200, r.text
    (call,) = wrapper.add_calls
    assert (call["document_id"], call["update_mode"]) == ("notes", "append")


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


# ── the acknowledgement means "applied", not "forwarded" ───────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"text": "x", "append_document_id": "hermes-s1"},
        {"text": "x", "document_id": "notes", "update_mode": "append"},
    ],
)
def test_the_route_does_not_acknowledge_on_the_providers_behalf(
    http: TestClient, wrapper: RecordingWrapper, body: dict[str, Any]
) -> None:
    """The route forwarded the mode; this provider reported nothing. The
    response must not claim the append happened."""
    r = http.post("/api/memory/add", json=body, headers=PRIVATE)
    assert r.status_code == 200, r.text
    assert wrapper.add_calls[0]["update_mode"] == "append"
    assert "update_mode" not in r.json()


def test_the_hindsight_provider_acknowledges_what_it_sent_to_the_engine() -> None:
    engine = _engine()
    with TestClient(_app(HindsightProvider(client=engine, unified_bank=True))) as http:
        appended = http.post(
            "/api/memory/add",
            json={"text": "x", "append_document_id": "hermes-s1"},
            headers=PRIVATE,
        )
        plain = http.post("/api/memory/add", json={"text": "x", "document_id": "notes"})
    assert appended.json()["update_mode"] == "append"
    assert engine.retain.await_args_list[0].kwargs["update_mode"] == "append"
    assert "update_mode" not in plain.json()
    assert "update_mode" not in engine.retain.await_args_list[1].kwargs


def test_the_volatile_fallback_does_not_acknowledge() -> None:
    """The in-memory fallback has no documents and ignores the mode."""
    with TestClient(_app(PgVectorProvider())) as http:
        r = http.post(
            "/api/memory/add",
            json={"text": "x", "append_document_id": "hermes-s1"},
            headers=PRIVATE,
        )
    assert r.status_code == 200, r.text
    assert "update_mode" not in r.json()


# ── plugin → route → provider → engine ─────────────────────────────────────


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
    # One tag set across all four, after the write envelope has had its say,
    # so the engine's re-stamp changes nothing.
    tag_sets = {tuple(sorted(r["tags"])) for r in retains}
    assert tag_sets == {("agent:hermes", "kind:ephemeral", "visibility:private")}
    assert retains[0]["content"] == "User: question 0\nAssistant: answer 0"
    # The write kind reached the engine as metadata, not as a tag.
    assert [r["metadata"]["lifecycle"] for r in retains] == ["chat"] * 3 + ["session-end"]
    # The plugin saw the provider's acknowledgement and kept appending.
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


def test_on_the_volatile_fallback_the_plugin_stops_asking_to_append() -> None:
    provider = PgVectorProvider()
    with TestClient(_app(provider)) as http:
        plugin = _plugin(http, agent_id="hermes", session_id="s1")
        plugin.sync_turn("q", "a")
        assert plugin._session_documents is False
