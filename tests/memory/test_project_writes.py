"""Project as a request field, on the wire (ADR-0058 decision 4a).

Every test here drives the real REST client over a mock transport, so what is
asserted is the JSON body the engine would receive: through
``HindsightProvider.add``, ``POST /api/memory/add``, the MCP ``memory_add``
tool, and ``HindsightProvider.retain_items`` (one engine call for a batch).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.errors import BadRequest, MemoryEngineUnavailable
from hal0.mcp import memory as memory_mcp
from hal0.memory.hindsight_client import HindsightRestClient
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.projects import ProjectRegistry, ProjectResolution, resolve_project

SESSION = "3f2b8c1e-5a47-4d09-9e6b-7c1a2d3e4f50"
OPERATION = "0b9f6c1e-2d3a-4b5c-8d7e-9f0a1b2c3d4e"

REGISTRY = ProjectRegistry(
    aliases={"atlas-mono": "atlas"},
    registered=frozenset({"atlas"}),
)


class _Engine:
    """A mock engine: records every retain request the REST client posts."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.paths: list[str] = []
        self.raw: list[bytes] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/memories")
        self.paths.append(request.url.path)
        self.raw.append(request.content)
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "engine says no"})
        return httpx.Response(
            200,
            json={
                "success": True,
                "bank_id": "shared",
                "items_count": len(body["items"]),
                "async": bool(body.get("async")),
                "operation_id": body.get("operation_id") or "op-1",
            },
        )

    @property
    def item(self) -> dict[str, Any]:
        (item,) = self.bodies[-1]["items"]
        return item


def _wired(
    *, unified: bool = True, registry: ProjectRegistry | None = REGISTRY, status: int = 200
) -> tuple[_Engine, HindsightProvider]:
    engine = _Engine(status)
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine.test")
    client = HindsightRestClient(base_url="http://engine.test", http_client=http)
    return engine, HindsightProvider(client=client, unified_bank=unified, projects=registry)


# ── HindsightProvider.add ───────────────────────────────────────────────────


async def test_add_without_a_project_is_global() -> None:
    """The one change for a caller that passes nothing new: a durable write
    with no project is stamped ``scope:global``."""
    engine, provider = _wired()
    await provider.add("note", tags=["topic:cats"], client_id="hermes")
    item = engine.item
    assert item["tags"] == ["topic:cats", "agent:hermes", "kind:durable", "scope:global"]
    assert item["observation_scopes"] == "shared"
    # Nothing was decided about a project, so nothing is recorded.
    assert "metadata" not in item


async def test_add_with_a_project_stamps_exactly_one_project_tag() -> None:
    engine, provider = _wired()
    await provider.add(
        "note",
        tags=["topic:cats", "project:something-else", "Project:Third"],
        client_id="pi",
        project="Atlas Mono",
    )
    item = engine.item
    assert item["tags"] == ["topic:cats", "agent:pi", "kind:durable", "project:atlas"]
    assert item["observation_scopes"] == [["project:atlas"]]
    assert item["metadata"]["project"] == "atlas"
    assert item["metadata"]["project_source"] == "field"


async def test_an_ephemeral_project_write_keeps_its_project() -> None:
    engine, provider = _wired()
    await provider.add("note", tags=["checkpoint"], client_id="pi", project="atlas")
    item = engine.item
    assert item["tags"] == ["agent:pi", "kind:ephemeral", "project:atlas"]
    assert item["observation_scopes"] == [["project:atlas"]]
    assert item["metadata"]["lifecycle"] == "checkpoint"


async def test_an_ephemeral_write_with_no_project_gets_neither_tag() -> None:
    engine, provider = _wired()
    await provider.add("note", tags=[SESSION, "session-end"], client_id="hermes")
    assert engine.item["tags"] == ["agent:hermes", "kind:ephemeral"]
    assert engine.item["observation_scopes"] == "shared"


async def test_a_denied_project_name_is_no_project() -> None:
    engine, provider = _wired()
    await provider.add("note", client_id="pi", project="tmp")
    item = engine.item
    assert item["tags"] == ["agent:pi", "kind:durable", "scope:global"]
    assert item["metadata"]["project_source"] == "denied"
    assert "project" not in item["metadata"]


@pytest.mark.parametrize(
    ("tags", "final"),
    [
        # A single project tag is the caller naming its project the old way:
        # it goes through the same resolver as the field.
        (["project:atlas"], ["agent:pi", "kind:durable", "project:atlas"]),
        (["project:Atlas"], ["agent:pi", "kind:durable", "project:atlas"]),
        (["project:atlas-mono", "slots"], ["slots", "agent:pi", "kind:durable", "project:atlas"]),
        (["project:tmp"], ["agent:pi", "kind:durable", "scope:global"]),
        (["project:unresolved"], ["agent:pi", "kind:durable", "scope:global"]),
        # Two or more name no single project and pass through as sent.
        (
            ["project:atlas", "project:borealis"],
            ["project:atlas", "project:borealis", "agent:pi", "kind:durable"],
        ),
    ],
)
async def test_caller_project_tags(tags, final) -> None:
    engine, provider = _wired()
    await provider.add("note", tags=tags, client_id="pi")
    assert engine.item["tags"] == final


async def test_the_default_registry_applies_when_none_is_configured() -> None:
    engine, provider = _wired(registry=None)
    assert provider.projects == ProjectRegistry()
    await provider.add("note", client_id="pi", project="Atlas Mono")
    await provider.add("note", client_id="pi", project="scratch")
    first, second = (body["items"][0] for body in engine.bodies)
    assert first["tags"] == ["agent:pi", "kind:durable", "project:atlas-mono"]
    assert second["tags"] == ["agent:pi", "kind:durable", "scope:global"]


async def test_a_coding_client_with_no_project_is_unresolved() -> None:
    engine, provider = _wired()
    await provider.add("note", client_id="claude-code", coding=True)
    await provider.add("note", tags=["checkpoint"], client_id="claude-code", coding=True)
    await provider.add("note", client_id="claude-code", coding=True, project="workdir")
    await provider.add("note", client_id="claude-code", coding=True, project="atlas")
    durable, ephemeral, denied, resolved = (body["items"][0] for body in engine.bodies)
    assert durable["tags"] == ["agent:claude-code", "kind:durable", "project:unresolved"]
    assert durable["observation_scopes"] == [["project:unresolved"]]
    assert durable["metadata"]["project"] == "unresolved"
    assert durable["metadata"]["project_source"] == "none"
    assert ephemeral["tags"] == ["agent:claude-code", "kind:ephemeral", "project:unresolved"]
    assert denied["tags"] == ["agent:claude-code", "kind:durable", "project:unresolved"]
    assert denied["metadata"]["project_source"] == "denied"
    assert resolved["tags"] == ["agent:claude-code", "kind:durable", "project:atlas"]
    assert not any("scope:global" in item["tags"] for item in (durable, ephemeral, denied))


async def test_caller_metadata_keys_are_not_overwritten() -> None:
    engine, provider = _wired()
    await provider.add(
        "note", client_id="pi", project="atlas", metadata={"project": "mine", "repo": "x"}
    )
    metadata = engine.item["metadata"]
    assert metadata["project"] == "mine" and metadata["project_source"] == "field"
    assert engine.item["tags"][-1] == "project:atlas"


async def test_a_private_project_write_keeps_the_private_scope_guard() -> None:
    """A private note in the unified bank is sent with no observation scope,
    project or not, so its observations keep ``visibility:private``."""
    engine, provider = _wired()
    await provider.add(
        "secret",
        dataset="private:alice",
        client_id="alice",
        project="atlas",
        observation_scopes=[["project:atlas"]],
    )
    await provider.add("secret", dataset="private:alice", client_id="alice")
    with_project, without = (body["items"][0] for body in engine.bodies)
    assert with_project["tags"] == [
        "agent:alice",
        "kind:durable",
        "project:atlas",
        "visibility:private",
    ]
    assert without["tags"] == ["agent:alice", "kind:durable", "scope:global", "visibility:private"]
    assert "observation_scopes" not in with_project and "observation_scopes" not in without


async def test_split_banks_stamp_a_named_project_too() -> None:
    engine, provider = _wired(unified=False)
    await provider.add("note", client_id="pi", project="atlas")
    await provider.add("note", client_id="pi")
    named, plain = (body["items"][0] for body in engine.bodies)
    assert named["tags"] == ["agent:pi", "kind:durable", "project:atlas"]
    assert plain["tags"] == ["agent:pi", "kind:durable", "scope:global"]


# ── the retired project:<id> namespace (direct provider callers only) ──────


async def test_a_project_namespace_write_keeps_its_tag_verbatim_and_readable() -> None:
    """The unified read filter matches the namespace name against the tag
    verbatim, so the namespace's id is stamped as written, not normalised."""
    engine, provider = _wired()
    await provider.add(
        "note",
        dataset="project:Apollo",
        tags=["project:other"],
        client_id="pi",
        project="atlas",
    )
    item = engine.item
    assert item["tags"] == ["agent:pi", "kind:durable", "project:Apollo"]
    assert item["metadata"]["project_source"] == "namespace"
    assert item["observation_scopes"] == [["project:Apollo"]]
    assert provider._is_in_scope({"tags": item["tags"]}, {"project:Apollo"}, False)


async def test_a_project_bank_write_gets_no_project_tag_and_no_global_stamp() -> None:
    """Pre-unified mode isolates a project by its bank; a tag would be a
    second owner of the same fact, and the write is not global either."""
    engine, provider = _wired(unified=False)
    await provider.add("note", dataset="project:apollo", tags=["topic:cats"], client_id="pi")
    assert engine.paths[-1].endswith("/banks/project__apollo/memories")
    assert engine.item["tags"] == ["topic:cats", "agent:pi", "kind:durable"]


# ── POST /api/memory/add ────────────────────────────────────────────────────


async def _rest_add(
    provider: Any, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        return await client.post(
            "http://test/api/memory/add",
            json=body,
            headers={"X-hal0-Agent": "pi", **(headers or {})},
        )


async def test_rest_add_with_a_project_field() -> None:
    engine, provider = _wired()
    response = await _rest_add(
        provider, {"text": "note", "tags": ["slots", "project:stale"], "project": "Atlas Mono"}
    )
    assert response.status_code == 200, response.text
    assert engine.item["tags"] == ["slots", "agent:pi", "kind:durable", "project:atlas"]
    assert engine.item["observation_scopes"] == [["project:atlas"]]
    assert engine.item["metadata"]["project_source"] == "field"


async def test_rest_add_without_a_project() -> None:
    engine, provider = _wired()
    response = await _rest_add(provider, {"text": "note", "tags": ["slots"]})
    assert response.status_code == 200, response.text
    assert engine.item["tags"] == ["slots", "agent:pi", "kind:durable", "scope:global"]
    assert engine.item["observation_scopes"] == "shared"


@pytest.mark.parametrize(
    ("body", "headers", "project_tag"),
    [
        ({}, {"X-hal0-Project": "atlas"}, "project:atlas"),
        ({}, {"x-hal0-project": "  Atlas Mono  "}, "project:atlas"),
        # The body field wins over the header.
        ({"project": "borealis"}, {"X-hal0-Project": "atlas"}, "project:borealis"),
        # A blank field names nothing, so the header stands.
        ({"project": "   "}, {"X-hal0-Project": "atlas"}, "project:atlas"),
        ({"project": ""}, {"X-hal0-Project": "atlas"}, "project:atlas"),
        ({"project": None}, {"X-hal0-Project": "atlas"}, "project:atlas"),
        ({}, {"X-hal0-Project": "tmp"}, "scope:global"),
        ({}, {"X-hal0-Project": "   "}, "scope:global"),
    ],
)
async def test_rest_add_project_header(body, headers, project_tag) -> None:
    engine, provider = _wired()
    response = await _rest_add(provider, {"text": "note", **body}, headers)
    assert response.status_code == 200, response.text
    assert engine.item["tags"] == ["agent:pi", "kind:durable", project_tag]


@pytest.mark.parametrize("project", [7, ["atlas"], {"name": "atlas"}, True])
async def test_rest_add_refuses_a_project_that_is_not_a_string(project) -> None:
    engine, provider = _wired()
    response = await _rest_add(provider, {"text": "note", "project": project})
    assert response.status_code == 400
    assert "'project' must be a string" in response.text
    assert engine.bodies == []


class _KeywordRecorder:
    """A provider double that records the keywords ``add`` was called with."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "doc-1", "timestamp": "2026-01-01T00:00:00+00:00"}


async def test_the_project_is_passed_on_only_when_the_caller_named_one() -> None:
    """A provider that predates the field is never handed it by a caller that
    named no project, on either entry point."""
    provider = _KeywordRecorder()
    dispatch = memory_mcp.make_dispatcher(provider, client_id_resolver=lambda: "pi")
    assert (await _rest_add(provider, {"text": "note"})).status_code == 200
    assert (await dispatch("memory_add", {"text": "note"}))["status"] == "ok"
    assert (await _rest_add(provider, {"text": "note", "project": "atlas"})).status_code == 200
    assert (await dispatch("memory_add", {"text": "note", "project": "atlas"}))["status"] == "ok"
    rest_plain, mcp_plain, rest_named, mcp_named = provider.calls
    assert "project" not in rest_plain and "project" not in mcp_plain
    assert rest_named["project"] == "atlas" and mcp_named["project"] == "atlas"
    # No entry point sets the coding flag yet; the provider default applies.
    assert not any("coding" in call for call in provider.calls)


# ── MCP memory_add ──────────────────────────────────────────────────────────


def _mcp(provider: HindsightProvider, *, private: bool = False):
    return memory_mcp.make_dispatcher(
        provider,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: private,
    )


async def test_mcp_add_with_a_project() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)(
        "memory_add",
        {"text": "note", "tags": ["slots", "project:stale"], "project": "Atlas Mono"},
    )
    assert out["status"] == "ok", out
    assert engine.item["tags"] == ["slots", "agent:pi-coder", "kind:durable", "project:atlas"]
    assert engine.item["observation_scopes"] == [["project:atlas"]]
    assert engine.item["metadata"]["project"] == "atlas"


async def test_mcp_add_without_a_project() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)("memory_add", {"text": "note", "project": "   "})
    assert out["status"] == "ok", out
    assert engine.item["tags"] == ["agent:pi-coder", "kind:durable", "scope:global"]


async def test_mcp_private_add_with_a_project() -> None:
    engine, provider = _wired()
    out = await _mcp(provider, private=True)("memory_add", {"text": "note", "project": "atlas"})
    assert out["status"] == "ok", out
    assert engine.item["tags"] == [
        "agent:pi-coder",
        "kind:durable",
        "project:atlas",
        "visibility:private",
    ]
    assert "observation_scopes" not in engine.item


async def test_mcp_add_refuses_a_project_that_is_not_a_string() -> None:
    engine, provider = _wired()
    out = await _mcp(provider)("memory_add", {"text": "note", "project": ["atlas"]})
    assert out["status"] == "error" and out["error"]["code"] == "mcp.memory_schema"
    assert engine.bodies == []


def test_the_mcp_tool_advertises_the_project_parameter() -> None:
    tool = memory_mcp._memory_tool_definitions()["memory_add"]
    assert "project" in tool.parameters["properties"]
    assert "project" not in tool.parameters.get("required", [])


# ── HindsightProvider.retain_items ──────────────────────────────────────────


def _items() -> list[dict[str, Any]]:
    return [
        {
            "content": "first",
            "document_id": "doc-1",
            "context": "a coding session",
            "tags": [SESSION, "checkpoint", "harness:claude-code", "agent:forged"],
            "metadata": {"turn": 3, "files": ["a.py", "b.py"]},
            "timestamp": "2026-01-02T03:04:05+00:00",
        },
        {
            "content": "second",
            "document_id": "doc-2",
            "tags": ["decision", "project:stale", "visibility:private"],
            "entities": [{"text": "hal0", "type": "project"}],
            "strategy": "verbatim",
            "update_mode": "append",
        },
        {"content": "third"},
    ]


async def test_retain_items_makes_one_engine_call_for_the_batch() -> None:
    engine, provider = _wired()
    out = await provider.retain_items(
        _items(),
        agent="claude-code",
        project_resolution=resolve_project(field="Atlas Mono", registry=REGISTRY, coding=True),
        coding=True,
        async_=True,
        operation_id=OPERATION,
    )
    assert len(engine.bodies) == 1
    assert engine.paths == ["/v1/default/banks/shared/memories"]
    body = engine.bodies[0]
    assert set(body) == {"items", "async", "operation_id"}
    first, second, third = body["items"]

    # Each item went through the envelope: identity, kind, session id and
    # lifecycle word, reserved tags, and the one project tag.
    assert first["tags"] == [
        "harness:claude-code",
        "agent:claude-code",
        "kind:ephemeral",
        "project:atlas",
    ]
    assert first["metadata"] == {
        "turn": "3",
        "files": '["a.py", "b.py"]',
        "source": "claude-code",
        "project": "atlas",
        "project_source": "field",
        "session_id": SESSION,
        "lifecycle": "checkpoint",
    }
    assert first["document_id"] == "doc-1"
    assert first["context"] == "a coding session"
    assert first["timestamp"] == "2026-01-02T03:04:05+00:00"
    assert first["observation_scopes"] == [["project:atlas"]]

    assert second["tags"] == ["decision", "agent:claude-code", "kind:durable", "project:atlas"]
    assert second["entities"] == [{"text": "hal0", "type": "project"}]
    assert second["strategy"] == "verbatim"
    assert second["update_mode"] == "append"
    assert second["context"] == "claude-code"  # the source, as add defaults it

    assert third["tags"] == ["agent:claude-code", "kind:durable", "project:atlas"]
    assert third["document_id"] and third["timestamp"]

    assert out["document_ids"] == ["doc-1", "doc-2", third["document_id"]]
    assert out["operation_id"] == OPERATION
    assert out["items_count"] == 3
    assert out["engine_response"]["success"] is True


@pytest.mark.parametrize(
    ("async_", "operation_id", "fragments", "absent"),
    [
        (True, OPERATION, [b'"async":true', f'"operation_id":"{OPERATION}"'.encode()], []),
        (False, OPERATION, [b'"async":false', f'"operation_id":"{OPERATION}"'.encode()], []),
        # Not a UUID, upper case, padded: hal0 does not judge or reshape it.
        (True, " Not-A-UUID ", [b'"operation_id":" Not-A-UUID "'], []),
        (True, OPERATION.upper(), [f'"operation_id":"{OPERATION.upper()}"'.encode()], []),
        (None, None, [], [b'"async"', b'"operation_id"']),
        (True, None, [b'"async":true'], [b'"operation_id"']),
        (None, OPERATION, [f'"operation_id":"{OPERATION}"'.encode()], [b'"async"']),
    ],
)
async def test_retain_items_forwards_async_and_operation_id_unchanged(
    async_, operation_id, fragments, absent
) -> None:
    engine, provider = _wired()
    await provider.retain_items(
        [{"content": "x"}], agent="pi", async_=async_, operation_id=operation_id
    )
    raw = engine.raw[0]
    for fragment in fragments:
        assert fragment in raw
    for fragment in absent:
        assert fragment not in raw
    body = engine.bodies[0]
    assert body.get("async") is async_
    assert body.get("operation_id") == operation_id


async def test_a_resend_carries_the_same_operation_id_byte_for_byte() -> None:
    """The engine keys an async retain on ``operation_id``; a retry must send
    the identical id or it enqueues a duplicate."""
    engine, provider = _wired()
    for _ in range(2):
        await provider.retain_items(
            [{"content": "x", "document_id": "doc-1"}],
            agent="pi",
            async_=True,
            operation_id=OPERATION,
        )
    needle = f'"operation_id":"{OPERATION}"'.encode()
    assert all(needle in raw for raw in engine.raw)
    assert [body["operation_id"] for body in engine.bodies] == [OPERATION, OPERATION]


async def test_retain_items_normalises_an_item_exactly_as_add_does() -> None:
    engine, provider = _wired()
    fields = {
        "tags": [SESSION, "pre-compress", "topic:cats", "project:atlas-mono"],
        "metadata": {"turn": 1, "context": "ctx", "timestamp": "2026-01-02T03:04:05+00:00"},
        "document_id": "doc-9",
        "observation_scopes": [["agent:victim"], ["topic:cats"]],
    }
    await provider.add("same", source="pi", client_id="pi", **fields)
    await provider.retain_items([{"content": "same", **fields}], agent="pi")
    single, batched = (body["items"][0] for body in engine.bodies)
    assert single == batched
    assert single["tags"] == ["topic:cats", "agent:pi", "kind:ephemeral", "project:atlas"]
    assert single["observation_scopes"] == [["topic:cats"]]  # the reserved scope was stripped
    assert single["context"] == "ctx" and "context" not in single["metadata"]
    # add's single-item request is async by default; a batch sends what it is given.
    assert engine.bodies[0]["async"] is True and "async" not in engine.bodies[1]


async def test_an_item_resolves_its_own_project_tag_when_the_request_named_none() -> None:
    engine, provider = _wired()
    await provider.retain_items(
        [
            {"content": "a", "tags": ["project:Atlas-Mono"]},
            {"content": "b"},
            {"content": "c", "tags": ["project:tmp"]},
            {"content": "d", "tags": ["project:atlas", "project:borealis"]},
        ],
        agent="claude-code",
        project_resolution=resolve_project(registry=REGISTRY, coding=True),
        coding=True,
    )
    a, b, c, d = engine.bodies[0]["items"]
    assert a["tags"] == ["agent:claude-code", "kind:durable", "project:atlas"]
    assert a["metadata"]["project_source"] == "tag"
    assert b["tags"] == ["agent:claude-code", "kind:durable", "project:unresolved"]
    assert c["tags"] == ["agent:claude-code", "kind:durable", "project:unresolved"]
    assert c["metadata"]["project_source"] == "denied"
    assert d["tags"] == ["project:atlas", "project:borealis", "agent:claude-code", "kind:durable"]
    assert [item["observation_scopes"] for item in (a, b, c)] == [
        [["project:atlas"]],
        [["project:unresolved"]],
        [["project:unresolved"]],
    ]


async def test_a_refused_request_project_is_not_rescued_by_an_item_tag() -> None:
    engine, provider = _wired()
    await provider.retain_items(
        [{"content": "a", "tags": ["project:atlas"]}],
        agent="pi",
        project_resolution=resolve_project(field="tmp", registry=REGISTRY),
    )
    assert engine.item["tags"] == ["agent:pi", "kind:durable", "scope:global"]


async def test_retain_items_without_a_resolution_behaves_like_add() -> None:
    engine, provider = _wired()
    await provider.retain_items([{"content": "a"}, {"content": "b", "tags": ["chat"]}], agent="pi")
    durable, ephemeral = engine.bodies[0]["items"]
    assert durable["tags"] == ["agent:pi", "kind:durable", "scope:global"]
    assert ephemeral["tags"] == ["agent:pi", "kind:ephemeral"]
    assert durable["observation_scopes"] == ephemeral["observation_scopes"] == "shared"


async def test_retain_items_keeps_the_private_write_scope_guard() -> None:
    engine, provider = _wired()
    out = await provider.retain_items(
        [
            {"content": "a", "document_id": "doc-1", "observation_scopes": "per_tag"},
            {"content": "b", "tags": ["visibility:shared"], "observation_scopes": [["x"]]},
        ],
        agent="alice",
        dataset="private:alice",
        project_resolution=ProjectResolution(slug="atlas", source="field"),
    )
    first, second = engine.bodies[0]["items"]
    for item in (first, second):
        assert item["tags"] == [
            "agent:alice",
            "kind:durable",
            "project:atlas",
            "visibility:private",
        ]
        assert "observation_scopes" not in item
    assert first["document_id"] == "private:alice:doc-1"
    assert out["document_ids"][0] == "private:alice:doc-1"


async def test_a_shared_batch_may_not_claim_a_private_document_id() -> None:
    engine, provider = _wired()
    with pytest.raises(ValueError, match="reserved for private documents"):
        await provider.retain_items(
            [{"content": "ok"}, {"content": "x", "document_id": "private:alice:doc-1"}],
            agent="mallory",
        )
    assert engine.bodies == []


@pytest.mark.parametrize(
    "items",
    [
        [],
        None,
        "content",
        [{"content": ""}],
        [{"content": "ok"}, {"text": "wrong field"}],
        [{"content": 3}],
        ["content"],
    ],
)
async def test_retain_items_refuses_a_malformed_batch_before_any_engine_call(items) -> None:
    engine, provider = _wired()
    with pytest.raises(BadRequest):
        await provider.retain_items(items, agent="pi")
    assert engine.bodies == []


async def test_retain_items_keeps_the_write_failure_bookkeeping() -> None:
    engine, provider = _wired(status=422)
    assert provider.write_degraded is False
    with pytest.raises(httpx.HTTPStatusError):
        await provider.retain_items([{"content": "a"}], agent="pi")
    assert len(engine.bodies) == 1
    assert provider.write_degraded is True
    assert provider._last_write_reason == "retain_failed"
    # A 4xx is an answer, so the engine is reachable: only the write path is
    # marked, exactly as for a single add.
    assert provider.degraded is False


async def test_retain_items_maps_a_connect_failure_like_add() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://engine.test")
    provider = HindsightProvider(
        client=HindsightRestClient(base_url="http://engine.test", http_client=http),
        unified_bank=True,
    )
    with pytest.raises(MemoryEngineUnavailable):
        await provider.retain_items([{"content": "a"}], agent="pi")
    assert provider.write_degraded is True and provider.degraded is True


async def test_retain_items_goes_to_the_configured_platform_bank() -> None:
    engine = _Engine()
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine.test")
    provider = HindsightProvider(
        client=HindsightRestClient(base_url="http://engine.test", http_client=http),
        unified_bank=True,
        bank="platform-bank",
    )
    await provider.retain_items([{"content": "a"}, {"content": "b"}], agent="pi")
    assert engine.paths == ["/v1/default/banks/platform-bank/memories"]
