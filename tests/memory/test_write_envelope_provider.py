"""The write envelope is applied by ``HindsightProvider.add`` (ADR-0058 decision 4).

Two layers of proof:

* provider tests against the recording engine double the other provider tests
  use, pinning the final tags, the metadata additions, the document id and the
  observation scope;
* entry-point tests that drive ``POST /api/memory/add`` and the MCP
  ``memory_add`` tool through the real REST client over a mock transport, so
  what is asserted is the JSON body the engine would receive.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.mcp import memory as memory_mcp
from hal0.memory.hindsight_client import HindsightRestClient
from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient

SESSION = "3f2b8c1e-5a47-4d09-9e6b-7c1a2d3e4f50"
OTHER_SESSION = "019a2b3c-4d5e-7f60-8a1b-2c3d4e5f6071"


def _unified() -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), unified_bank=True)


def _legacy() -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), unified_bank=False)


# ── Provider: tags and metadata ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_unified, _legacy])
async def test_add_applies_the_envelope(make) -> None:
    p = make()
    await p.add(
        "the slot layer pins a device per lane",
        tags=[SESSION, "checkpoint", "hal0", "source:chat"],
        client_id="hermes",
    )
    rec = p._client.retained[-1]
    assert rec["tags"] == ["hal0", "source:chat", "agent:hermes", "kind:ephemeral"]
    assert rec["metadata"]["session_id"] == SESSION
    assert rec["metadata"]["lifecycle"] == "checkpoint"
    assert rec["observation_scopes"] == "shared"


@pytest.mark.asyncio
async def test_a_plain_write_is_durable_and_gains_only_the_required_tags() -> None:
    p = _unified()
    await p.add("x", tags=["hal0", "H0-229"], client_id="hermes")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["hal0", "H0-229", "agent:hermes", "kind:durable"]
    assert rec["metadata"] == {}


@pytest.mark.asyncio
async def test_a_write_with_no_tags_still_carries_the_required_pair() -> None:
    p = _unified()
    await p.add("x", client_id="hermes")
    assert p._client.retained[-1]["tags"] == ["agent:hermes", "kind:durable"]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_id", [None, "anonymous"])
async def test_an_unresolved_identity_keeps_the_unknown_fallback(client_id) -> None:
    p = _unified()
    await p.add("x", tags=["agent:brain"], client_id=client_id)
    assert p._client.retained[-1]["tags"] == ["agent:unknown", "kind:durable"]


@pytest.mark.asyncio
async def test_several_session_tags_land_under_one_metadata_key() -> None:
    p = _unified()
    await p.add("x", tags=[SESSION, f"session:{OTHER_SESSION}"], client_id="codex")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["agent:codex", "kind:durable"]
    assert "session_id" not in rec["metadata"]
    assert json.loads(rec["metadata"]["session_ids"]) == [SESSION, OTHER_SESSION]


@pytest.mark.asyncio
async def test_a_caller_kind_tag_is_honoured() -> None:
    p = _unified()
    await p.add("x", tags=["kind:ephemeral", "scratch"], client_id="hermes")
    assert p._client.retained[-1]["tags"] == ["scratch", "agent:hermes", "kind:ephemeral"]


@pytest.mark.asyncio
async def test_an_explicit_durable_keeps_a_lifecycle_word_as_a_topic_tag() -> None:
    """``chat`` on a write the caller marked durable is a topic, not a
    lifecycle marker: it stays a tag and nothing is moved to metadata."""
    p = _unified()
    await p.add(
        "the chat panel needs a retry button", tags=["chat", "kind:durable"], client_id="pi"
    )
    rec = p._client.retained[-1]
    assert rec["tags"] == ["chat", "agent:pi", "kind:durable"]
    assert "lifecycle" not in rec["metadata"]


@pytest.mark.asyncio
async def test_a_forged_visibility_tag_beside_a_lifecycle_word_is_dropped() -> None:
    p = _unified()
    await p.add("x", tags=[" Visibility:Private", "chat"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert tags == ["agent:openwebui", "kind:ephemeral"]
    assert not [t for t in tags if t.strip().lower().startswith("visibility:")]
    assert [t for t in tags if t.startswith("agent:")] == ["agent:openwebui"]


@pytest.mark.asyncio
async def test_a_uuid_tag_that_is_not_a_session_id_is_still_moved() -> None:
    """Documented behaviour: any bare UUID leaves the tag space, under the
    ``session_id`` metadata key, whatever it identifies."""
    document_uuid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    p = _unified()
    await p.add("x", tags=[document_uuid, "design-doc"], client_id="pi")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["design-doc", "agent:pi", "kind:durable"]
    assert rec["metadata"]["session_id"] == document_uuid


@pytest.mark.asyncio
async def test_an_md5_tag_stays_a_tag() -> None:
    p = _unified()
    await p.add("x", tags=["d41d8cd98f00b204e9800998ecf8427e"], client_id="pi")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["d41d8cd98f00b204e9800998ecf8427e", "agent:pi", "kind:durable"]
    assert rec["metadata"] == {}


@pytest.mark.asyncio
async def test_a_bare_string_of_tags_is_one_tag() -> None:
    p = _unified()
    await p.add("x", tags="hal0", client_id="hermes")  # type: ignore[arg-type]
    assert p._client.retained[-1]["tags"] == ["hal0", "agent:hermes", "kind:durable"]


@pytest.mark.asyncio
async def test_private_stamp_and_project_tag_are_unchanged() -> None:
    p = _unified()
    await p.add("x", dataset="private:alice", tags=["chat"], client_id="alice")
    await p.add("y", dataset="project:apollo", tags=["project:apollo"], client_id="alice")
    private, project = p._client.retained
    assert private["tags"] == ["agent:alice", "kind:ephemeral", "visibility:private"]
    assert project["tags"] == ["project:apollo", "agent:alice", "kind:durable"]


@pytest.mark.asyncio
async def test_metadata_naming_a_repo_does_not_become_a_project_tag() -> None:
    p = _unified()
    await p.add("x", tags=["hal0"], client_id="pi", metadata={"repo": "hal0", "project": "hal0"})
    assert p._client.retained[-1]["tags"] == ["hal0", "agent:pi", "kind:durable"]


# ── Provider: the document id is not derived from a tag ─────────────────────


@pytest.mark.asyncio
async def test_a_session_tag_does_not_become_the_document_id() -> None:
    """A session id lifted out of the tags is provenance. If it fed the
    ``<agent>:<session_id>`` join key, the second write would replace the
    first."""
    p = _unified()
    first = await p.add("turn one", tags=[SESSION], client_id="hermes")
    second = await p.add("turn two", tags=[SESSION], client_id="hermes")
    assert first["id"] != second["id"]
    assert SESSION not in first["id"] and SESSION not in second["id"]
    assert [r["metadata"]["session_id"] for r in p._client.retained] == [SESSION, SESSION]


@pytest.mark.asyncio
async def test_caller_session_metadata_still_drives_the_document_id() -> None:
    p = _unified()
    out = await p.add("x", tags=[OTHER_SESSION], client_id="hermes", metadata={"session_id": "s1"})
    rec = p._client.retained[-1]
    assert out["id"] == "hermes:s1"
    assert rec["metadata"]["session_id"] == "s1"
    assert json.loads(rec["metadata"]["session_ids"]) == [OTHER_SESSION]


@pytest.mark.asyncio
async def test_an_empty_caller_session_id_lets_the_tag_one_be_recorded() -> None:
    """The provider treats an empty ``session_id`` as absent when it derives
    the document id; the envelope agrees, so the tag-derived id is kept."""
    p = _unified()
    out = await p.add("x", tags=[SESSION], client_id="hermes", metadata={"session_id": ""})
    rec = p._client.retained[-1]
    assert rec["metadata"]["session_id"] == SESSION
    assert SESSION not in out["id"]
    assert not out["id"].startswith("hermes:")


# ── Provider: observation scopes ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", ["per_tag", "combined", "all_combinations", [["topic:cats"]]])
async def test_an_explicit_observation_scope_wins(explicit) -> None:
    p = _unified()
    await p.add("x", client_id="hermes", observation_scopes=explicit)
    assert p._client.retained[-1]["observation_scopes"] == explicit


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_unified, _legacy])
async def test_a_write_with_a_project_tag_gets_that_projects_scope(make) -> None:
    """ADR-0058 decision 4a: one observation scope per project, whatever other
    tags the write carries."""
    p = make()
    await p.add("x", tags=["project:hal0", "checkpoint", "slots"], client_id="pi")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["project:hal0", "slots", "agent:pi", "kind:ephemeral"]
    assert rec["observation_scopes"] == [["project:hal0"]]


@pytest.mark.asyncio
async def test_a_project_namespace_write_gets_that_projects_scope() -> None:
    p = _unified()
    await p.add("x", dataset="project:apollo", client_id="pi")
    rec = p._client.retained[-1]
    assert rec["tags"] == ["agent:pi", "kind:durable", "project:apollo"]
    assert rec["observation_scopes"] == [["project:apollo"]]


@pytest.mark.asyncio
async def test_a_write_under_two_projects_gets_one_scope_each() -> None:
    p = _unified()
    await p.add("x", tags=["project:hal0", "project:hal0-web"], client_id="pi")
    assert p._client.retained[-1]["observation_scopes"] == [["project:hal0"], ["project:hal0-web"]]


@pytest.mark.asyncio
async def test_a_write_with_no_project_gets_the_shared_scope() -> None:
    p = _unified()
    await p.add("x", tags=["hal0", "slots"], client_id="pi")
    assert p._client.retained[-1]["observation_scopes"] == "shared"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", ["shared", "per_tag", [["topic:cats"]]])
async def test_an_explicit_scope_wins_over_the_project_scope(explicit) -> None:
    p = _unified()
    await p.add("x", tags=["project:hal0"], client_id="pi", observation_scopes=explicit)
    assert p._client.retained[-1]["observation_scopes"] == explicit


@pytest.mark.asyncio
async def test_a_private_write_in_the_unified_bank_sends_no_scope() -> None:
    """Neither the untagged scope nor a project scope carries
    ``visibility:private``; a private note must not feed either."""
    p = _unified()
    await p.add("secret", dataset="private:alice", client_id="alice")
    await p.add("secret", dataset="private:alice", client_id="alice", observation_scopes="shared")
    await p.add("secret", dataset="private:alice", tags=["project:hal0"], client_id="alice")
    assert [rec["tags"][-1] for rec in p._client.retained] == ["visibility:private"] * 3
    assert all("observation_scopes" not in rec for rec in p._client.retained)


@pytest.mark.asyncio
async def test_a_private_write_with_list_form_scopes_sends_no_scope() -> None:
    """A caller's list-form scope is dropped whole on a private write, the
    harmless entry along with the one naming another agent."""
    p = _unified()
    await p.add(
        "secret",
        dataset="private:alice",
        client_id="alice",
        observation_scopes=[["agent:victim"], ["project:hal0"]],
    )
    assert "observation_scopes" not in p._client.retained[-1]


@pytest.mark.asyncio
async def test_a_private_project_write_stays_hidden_from_another_agent_in_that_project() -> None:
    """The envelope adds tags to a private write; the read filter must still
    see ``visibility:private`` and the owner, project tag or not."""
    p = _unified()
    await p.add(
        "alice private", dataset="private:alice", tags=["project:apollo"], client_id="alice"
    )
    await p.add("apollo note", dataset="project:apollo", client_id="alice")
    assert p._client.retained[0]["tags"] == [
        "project:apollo",
        "agent:alice",
        "kind:durable",
        "visibility:private",
    ]

    as_bob = await p.recall("x", dataset="project:apollo", client_id="bob")
    as_alice = await p.recall("x", dataset="project:apollo", client_id="alice")
    listed_by_bob = await p.list_items(dataset="project:apollo", client_id="bob")
    assert {i["text"] for i in as_bob} == {"apollo note"}
    assert {i["text"] for i in as_alice} == {"alice private", "apollo note"}
    assert {i["text"] for i in listed_by_bob["items"]} == {"apollo note"}


@pytest.mark.asyncio
async def test_a_private_write_to_its_own_bank_gets_the_default_scope() -> None:
    p = _legacy()
    await p.add("secret", dataset="private:alice", client_id="alice")
    rec = p._client.retained[-1]
    assert rec["bank_id"] == "private__alice"
    assert rec["observation_scopes"] == "shared"


# ── Entry points: what the engine receives on the wire ──────────────────────


class _Engine:
    """A mock engine: records every retain body the REST client posts."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/memories")
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "operation_id": "op-1"})

    @property
    def item(self) -> dict[str, Any]:
        (item,) = self.bodies[-1]["items"]
        return item


def _wired() -> tuple[_Engine, HindsightProvider]:
    engine = _Engine()
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine.test")
    client = HindsightRestClient(base_url="http://engine.test", http_client=http)
    return engine, HindsightProvider(client=client, unified_bank=True)


async def _rest_add(
    provider: HindsightProvider, body: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        return await client.post("http://test/api/memory/add", json=body, headers=headers)


@pytest.mark.asyncio
async def test_rest_add_reaches_the_engine_enveloped() -> None:
    engine, provider = _wired()
    response = await _rest_add(
        provider,
        {"text": "note", "tags": [SESSION, "session-end", "checkpoint", "hal0"]},
        {"X-hal0-Agent": "hermes"},
    )
    assert response.status_code == 200, response.text
    item = engine.item
    assert item["tags"] == ["hal0", "agent:hermes", "kind:ephemeral"]
    assert item["metadata"]["session_id"] == SESSION
    assert item["metadata"]["lifecycle"] == "session-end,checkpoint"
    assert item["observation_scopes"] == "shared"


@pytest.mark.asyncio
async def test_rest_add_with_a_project_tag_reaches_the_engine_project_scoped() -> None:
    engine, provider = _wired()
    response = await _rest_add(
        provider,
        {"text": "note", "tags": ["project:hal0", SESSION, "slots"]},
        {"X-hal0-Agent": "pi"},
    )
    assert response.status_code == 200, response.text
    item = engine.item
    assert item["tags"] == ["project:hal0", "slots", "agent:pi", "kind:durable"]
    assert item["observation_scopes"] == [["project:hal0"]]


@pytest.mark.asyncio
async def test_rest_add_without_identity_is_tagged_unknown() -> None:
    engine, provider = _wired()
    response = await _rest_add(provider, {"text": "note"}, {})
    assert response.status_code == 200, response.text
    assert engine.item["tags"] == ["agent:unknown", "kind:durable"]
    assert engine.item["observation_scopes"] == "shared"


@pytest.mark.asyncio
async def test_rest_private_add_keeps_its_stamp_and_sends_no_scope() -> None:
    engine, provider = _wired()
    response = await _rest_add(
        provider,
        {"text": "note", "tags": ["chat", "agent:hermes"]},
        {"X-hal0-Agent": "hermes", "X-hal0-Private": "1"},
    )
    assert response.status_code == 200, response.text
    item = engine.item
    assert item["tags"] == ["agent:hermes", "kind:ephemeral", "visibility:private"]
    assert item["metadata"]["lifecycle"] == "chat"
    assert "observation_scopes" not in item


def _mcp(provider: HindsightProvider, *, private: bool = False):
    return memory_mcp.make_dispatcher(
        provider,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: private,
    )


@pytest.mark.asyncio
async def test_mcp_add_reaches_the_engine_enveloped() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)(
        "memory_add",
        {"text": "note", "tags": [f"session:{SESSION}", "bootstrap", "self-report"]},
    )
    assert out["status"] == "ok", out
    item = engine.item
    assert item["tags"] == ["self-report", "agent:pi-coder", "kind:ephemeral"]
    assert item["metadata"]["session_id"] == SESSION
    assert item["metadata"]["lifecycle"] == "bootstrap"
    assert item["observation_scopes"] == "shared"


@pytest.mark.asyncio
async def test_mcp_add_with_a_project_tag_reaches_the_engine_project_scoped() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)("memory_add", {"text": "note", "tags": ["project:hal0", "slots"]})
    assert out["status"] == "ok", out
    item = engine.item
    assert item["tags"] == ["project:hal0", "slots", "agent:pi-coder", "kind:durable"]
    assert item["observation_scopes"] == [["project:hal0"]]


@pytest.mark.asyncio
async def test_mcp_add_forwards_an_explicit_observation_scope() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)(
        "memory_add", {"text": "note", "tags": ["hal0"], "observation_scopes": "per_tag"}
    )
    assert out["status"] == "ok", out
    assert engine.item["tags"] == ["hal0", "agent:pi-coder", "kind:durable"]
    assert engine.item["observation_scopes"] == "per_tag"


@pytest.mark.asyncio
async def test_mcp_private_add_keeps_its_stamp_and_sends_no_scope() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)("memory_add", {"text": "note", "tags": ["chat"], "private": True})
    assert out["status"] == "ok", out
    item = engine.item
    assert item["tags"] == ["agent:pi-coder", "kind:ephemeral", "visibility:private"]
    assert "observation_scopes" not in item
