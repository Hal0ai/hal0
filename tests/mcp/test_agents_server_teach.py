"""The ``hal0-agents`` teach tools: memory, skills, capabilities (ADR-0043 decision 10).

Like the run tools, the teach tools are a thin projection of ``BridgeService``
(``tests/bridge/test_service_teach.py`` pins the rules); these tests pin the
projection: the seven tool names after the nine run tools, their typed
arguments, the identity each call acts as, the inbox-gated skill writes, a
refusal raised (never returned as a success payload), and the secret mask and
16 KiB cap on the text a tool hands a remote machine.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from hal0.api.auth import ANON, AuthPrincipal
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService
from hal0.bridge.skills import SkillLibrary
from hal0.bridge.store import BridgeStore
from hal0.config.schema import BridgeConfig
from hal0.mcp import agents
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes
from tests.bridge.test_service_teach import FakeDispatcher, skill_md

RUN_TOOLS = [
    "agents_list",
    "agent_run",
    "agent_run_wait",
    "agent_run_status",
    "agent_run_events",
    "agent_run_answer",
    "agent_run_steer",
    "agent_run_stop",
    "agent_runs_list",
]
TEACH_TOOLS = [
    "agent_memory_add",
    "agent_memory_search",
    "agent_skills_list",
    "agent_skill_get",
    "agent_skill_put",
    "agent_skill_delete",
    "agent_capabilities",
]

CC = AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"agents"}))
OTHER = AuthPrincipal(tier="bridge", name="other", scopes=frozenset({"agents"}))
SECRET = "nlQ_0123456789abcdefSECRET"


def _payload(out: Any) -> dict[str, Any]:
    """A tool result as a dict, whichever shape FastMCP's ``call_tool`` hands back."""
    if isinstance(out, tuple):
        out = out[1] if isinstance(out[1], dict) else out[0]
    if isinstance(out, dict):
        return out
    parsed: dict[str, Any] = json.loads(out[0].text)
    return parsed


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def dropin(tmp_path: Path) -> Path:
    path = tmp_path / "dropin"
    path.mkdir()
    return path


@pytest.fixture
def shipped(tmp_path: Path) -> Path:
    path = tmp_path / "shipped"
    (path / "hal0-bench").mkdir(parents=True)
    (path / "hal0-bench" / "SKILL.md").write_text(
        skill_md("hal0-bench", "Bench."), encoding="utf-8"
    )
    return path


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
async def svc(
    tmp_path: Path,
    queue: ApprovalQueue,
    dropin: Path,
    shipped: Path,
    dispatcher: FakeDispatcher,
) -> AsyncIterator[BridgeService]:
    home = tmp_path / "hermes"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "config.yaml").write_text(
        "toolsets: [hermes-cli]\nmcp_servers:\n  hal0-memory:\n    headers:\n"
        f"      Authorization: Bearer {SECRET}\n",
        encoding="utf-8",
    )
    store = BridgeStore(tmp_path / "bridge.db")
    store.open()
    fake = FakeHermes()
    service = BridgeService(
        store=store,
        gateway=HermesGateway(
            home=home, environ=fake.environ(), transport=httpx.MockTransport(fake)
        ),
        config=BridgeConfig(),
        approval_queue=queue,
        events=None,
        memory_dispatcher=dispatcher,
        skills=SkillLibrary(shipped_root=shipped, dropin_root=dropin),
        detail_fetch=lambda _url: {"version": "0.22.4"},
    )
    yield service
    await service.close()


def _server(
    svc: Any, principal: AuthPrincipal | Callable[[], AuthPrincipal] | None = CC
) -> FastMCP:
    resolver = principal if callable(principal) else (lambda: principal or ANON)
    return agents.build_server(
        state=SimpleNamespace(bridge_service=svc), principal_resolver=resolver
    )


@pytest.fixture
def server(svc: BridgeService) -> FastMCP:
    return _server(svc, CC)


# -- the tool list -----------------------------------------------------------------------


async def test_sixteen_tools_listed_teach_tools_after_the_run_tools(server: FastMCP) -> None:
    names = [t.name for t in await server.list_tools()]
    assert len(names) == 16
    assert names == RUN_TOOLS + TEACH_TOOLS  # /api/bridge/me lists them in this order


async def test_teach_tool_schemas_are_typed(server: FastMCP) -> None:
    tools = {t.name: t for t in await server.list_tools()}
    for name in TEACH_TOOLS:
        assert tools[name].description, name
        assert "ctx" not in tools[name].inputSchema.get("properties", {}), name

    add = tools["agent_memory_add"].inputSchema
    assert set(add["properties"]) == {"agent", "text", "tags"}
    assert set(add["required"]) == {"agent", "text"}
    search = tools["agent_memory_search"].inputSchema
    assert set(search["properties"]) == {"agent", "query", "limit"}
    assert set(search["required"]) == {"agent", "query"}
    assert tools["agent_skills_list"].inputSchema.get("properties", {}) == {}
    assert set(tools["agent_skill_get"].inputSchema["required"]) == {"name"}
    put = tools["agent_skill_put"].inputSchema
    assert set(put["properties"]) == {"name", "skill_md", "files"}
    assert set(put["required"]) == {"name", "skill_md"}
    assert set(tools["agent_skill_delete"].inputSchema["required"]) == {"name"}
    assert set(tools["agent_capabilities"].inputSchema["required"]) == {"agent"}


async def test_annotations_tell_a_client_which_tools_only_read(server: FastMCP) -> None:
    tools = {t.name: t for t in await server.list_tools()}
    for name in (
        "agent_memory_search",
        "agent_skills_list",
        "agent_skill_get",
        "agent_capabilities",
    ):
        assert tools[name].annotations is not None and tools[name].annotations.readOnlyHint is True
    for name in ("agent_memory_add", "agent_skill_put", "agent_skill_delete"):
        assert tools[name].annotations is not None and tools[name].annotations.readOnlyHint is False
    assert tools["agent_skill_delete"].annotations.destructiveHint is True  # type: ignore[union-attr]


def test_definitions_still_built_once() -> None:
    assert agents._agents_tool_definitions() is agents._agents_tool_definitions()
    assert len(agents._agents_tool_definitions()) == 16


# -- memory --------------------------------------------------------------------------------


async def test_agent_memory_add_pins_the_namespace_and_tags_the_client(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    out = _payload(
        await server.call_tool(
            "agent_memory_add", {"agent": "hermes/coder", "text": "uses tabs", "tags": ["style"]}
        )
    )
    assert out["status"] == "ok" and out["id"] == "m1"
    [call] = dispatcher.calls
    assert call.tool == "memory_add"
    assert call.client_id == "hermes__coder" and call.private is True
    assert set(call.args["tags"]) == {"style", "source:bridge:cc"}


async def test_agent_memory_add_shared_and_default_tags(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    await server.call_tool("agent_memory_add", {"agent": "shared", "text": "a fact"})
    [call] = dispatcher.calls
    assert call.args["dataset"] == "shared" and call.private is False
    assert call.args["tags"] == ["source:bridge:cc"]


async def test_agent_memory_search_pins_the_namespace(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    out = _payload(
        await server.call_tool("agent_memory_search", {"agent": "hermes", "query": "tabs"})
    )
    assert out["results"][0]["id"] == "m1"
    [call] = dispatcher.calls
    assert call.client_id == "hermes" and call.private is True
    assert call.args == {"query": "tabs", "limit": 10}


async def test_agent_memory_search_masks_secrets_and_caps_text(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    dispatcher.answer = {
        "status": "ok",
        "results": [
            {"id": "m1", "text": f"curl -H 'Authorization: Bearer {SECRET}' " + "x" * 20000}
        ],
    }
    out = _payload(await server.call_tool("agent_memory_search", {"agent": "hermes", "query": "q"}))
    text = out["results"][0]["text"]
    assert SECRET not in text and "***REDACTED***" in text
    assert len(text.encode()) <= 16 * 1024 and out["truncated"] is True


async def test_agent_memory_unknown_agent_is_raised(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    with pytest.raises(Exception, match=r"bridge\.unknown_agent"):
        await server.call_tool("agent_memory_add", {"agent": "pi", "text": "x"})
    assert dispatcher.calls == []


async def test_agent_memory_refusal_is_an_error_not_a_payload(
    server: FastMCP, dispatcher: FakeDispatcher
) -> None:
    dispatcher.answer = {
        "status": "error",
        "error": {"code": "mcp.memory_schema", "detail": "text must be non-empty"},
    }
    with pytest.raises(Exception, match=r"bridge\.memory_invalid.*text must be non-empty"):
        await server.call_tool("agent_memory_add", {"agent": "hermes", "text": " "})


async def test_memory_off_is_bridge_memory_disabled(svc: BridgeService) -> None:
    svc.memory_dispatcher = None
    server = _server(svc)
    with pytest.raises(Exception, match=r"bridge\.memory_disabled"):
        await server.call_tool("agent_memory_search", {"agent": "hermes", "query": "q"})


# -- skills --------------------------------------------------------------------------------


async def test_agent_skills_list_and_get(server: FastMCP) -> None:
    listed = _payload(await server.call_tool("agent_skills_list", {}))
    assert [(s["name"], s["source"]) for s in listed["skills"]] == [("hal0-bench", "shipped")]
    got = _payload(await server.call_tool("agent_skill_get", {"name": "hal0-bench"}))
    assert got["name"] == "hal0-bench" and got["skill_md"].startswith("---")
    with pytest.raises(Exception, match=r"bridge\.skill_not_found"):
        await server.call_tool("agent_skill_get", {"name": "ghost"})


async def test_agent_skill_get_caps_and_masks_the_text(server: FastMCP, dropin: Path) -> None:
    big = skill_md("big-skill") + f"Authorization: Bearer {SECRET}\n" + "word " * 8000
    (dropin / "big-skill").mkdir()
    (dropin / "big-skill" / "SKILL.md").write_text(big, encoding="utf-8")
    got = _payload(await server.call_tool("agent_skill_get", {"name": "big-skill"}))
    assert SECRET not in got["skill_md"]
    assert len(got["skill_md"].encode()) <= 16 * 1024 and got["truncated"] is True


async def test_agent_skill_put_returns_pending_approval(
    server: FastMCP, queue: ApprovalQueue, dropin: Path
) -> None:
    out = _payload(
        await server.call_tool(
            "agent_skill_put",
            {"name": "forum-style", "skill_md": skill_md(), "files": {"notes.md": "n\n"}},
        )
    )
    assert out["status"] == "pending_approval" and out["name"] == "forum-style"
    [entry] = queue.list_pending()
    assert entry["id"] == out["approval_id"] and entry["tool"] == "agent_skill_put"
    assert entry["client_id"] == "bridge:cc" and entry["args"]["files"] == ["notes.md"]
    assert not (dropin / "forum-style").exists()

    await queue.approve(entry["id"])
    assert (dropin / "forum-style" / "notes.md").read_text() == "n\n"


async def test_agent_skill_put_invalid_is_raised_and_queues_nothing(
    server: FastMCP, queue: ApprovalQueue
) -> None:
    with pytest.raises(Exception, match=r"bridge\.skill_invalid"):
        await server.call_tool("agent_skill_put", {"name": "x", "skill_md": "no frontmatter"})
    assert queue.list_pending() == []


async def test_agent_skill_delete_is_gated_and_shipped_is_readonly(
    server: FastMCP, queue: ApprovalQueue, dropin: Path
) -> None:
    put = _payload(
        await server.call_tool("agent_skill_put", {"name": "forum-style", "skill_md": skill_md()})
    )
    await queue.approve(put["approval_id"])
    out = _payload(await server.call_tool("agent_skill_delete", {"name": "forum-style"}))
    assert out["status"] == "pending_approval" and (dropin / "forum-style").exists()
    await queue.approve(out["approval_id"])
    assert not (dropin / "forum-style").exists()

    with pytest.raises(Exception, match=r"bridge\.skill_readonly"):
        await server.call_tool("agent_skill_delete", {"name": "hal0-bench"})
    assert queue.list_pending() == []


# -- capabilities ----------------------------------------------------------------------------


async def test_agent_capabilities_has_no_secrets(server: FastMCP) -> None:
    out = _payload(await server.call_tool("agent_capabilities", {"agent": "hermes"}))
    assert out["agent"] == "hermes" and out["api_server_version"] == "0.22.4"
    assert out["mcp_servers"] == ["hal0-memory"] and out["toolsets_enabled"] == ["hermes-cli"]
    assert SECRET not in json.dumps(out) and "Bearer" not in json.dumps(out)
    named = _payload(await server.call_tool("agent_capabilities", {"agent": "hermes/coder"}))
    assert named["agent"] == "hermes/coder"
    with pytest.raises(Exception, match=r"bridge\.unknown_agent"):
        await server.call_tool("agent_capabilities", {"agent": "hermes/ghost"})


# -- identity and failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("agent_memory_add", {"agent": "hermes", "text": "x"}),
        ("agent_memory_search", {"agent": "hermes", "query": "x"}),
        ("agent_skills_list", {}),
        ("agent_skill_get", {"name": "hal0-bench"}),
        ("agent_skill_put", {"name": "forum-style", "skill_md": skill_md()}),
        ("agent_skill_delete", {"name": "forum-style"}),
        ("agent_capabilities", {"agent": "hermes"}),
    ],
)
async def test_an_anonymous_caller_is_refused_by_every_teach_tool(
    svc: BridgeService,
    dispatcher: FakeDispatcher,
    queue: ApprovalQueue,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    server = _server(svc, ANON)
    with pytest.raises(Exception, match=r"auth\.forbidden"):
        await server.call_tool(tool, arguments)
    assert dispatcher.calls == [] and queue.list_pending() == []


async def test_the_caller_is_whoever_the_resolver_says_on_each_call(
    svc: BridgeService, dispatcher: FakeDispatcher
) -> None:
    principals = iter([CC, OTHER])
    server = _server(svc, lambda: next(principals))
    await server.call_tool("agent_memory_add", {"agent": "shared", "text": "a"})
    await server.call_tool("agent_memory_add", {"agent": "shared", "text": "b"})
    assert [c.client_id for c in dispatcher.calls] == ["cc", "other"]
    assert [c.args["tags"] for c in dispatcher.calls] == [
        ["source:bridge:cc"],
        ["source:bridge:other"],
    ]


async def test_an_unexpected_failure_is_bridge_internal_without_its_text(
    svc: BridgeService, dispatcher: FakeDispatcher
) -> None:
    class Boom(FakeDispatcher):
        async def dispatch(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("psycopg connect failed at postgres://u:p@10.0.0.9/db")

        def scoped(self, client_id: str) -> Any:  # type: ignore[override]
            return self

    svc.memory_dispatcher = Boom()
    server = _server(svc)
    with pytest.raises(Exception) as e:
        await server.call_tool("agent_memory_search", {"agent": "hermes", "query": "q"})
    assert "bridge.internal" in str(e.value) and "postgres" not in str(e.value)
