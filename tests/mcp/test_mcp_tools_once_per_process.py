"""MCP tool definitions are built once per process, bound per app (#2396).

``mount_mcp_servers`` used to rebuild all ~209 FastMCP tools on every
``create_app()`` — ``Tool.from_function`` (signature inspection + a pydantic
model + its JSON schema per tool) was ~74% of app construction and ~31% of
the test suite's CPU. The fix separates the two halves:

* the tool *definitions* are built once per process and shared by every
  mounted server;
* each tool resolves its per-app state (approval queue, memory provider,
  memory dispatcher, REST base URL) through ``app.state`` at CALL time, via
  the FastMCP server that is handling the call — never through a closure
  captured at ``create_app()``.

These tests pin both halves: the second mount builds zero tools, and two
apps mounted in one process never see each other's state, even when a
field is swapped on ``app.state`` after the mount.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import base as tool_base

from hal0.api import mcp_mount
from hal0.api.auth import AuthPrincipal
from hal0.config.schema import Hal0Config
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


class _Provider:
    """Memory provider stand-in that records which instance served a call."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.list_calls = 0

    async def list_items(self, **_kwargs: Any) -> dict[str, Any]:
        self.list_calls += 1
        return {"items": [{"id": self.label}], "next_cursor": None}


def _mount(monkeypatch: pytest.MonkeyPatch, *, provider: Any = None) -> FastAPI:
    """Mount the MCP servers on a bare app the way ``create_app`` does."""
    monkeypatch.setattr(admin, "install_admin_route_map", lambda _app: None)
    app = FastAPI()
    app.state.hal0_config = Hal0Config()  # [bridge].enabled defaults on: /mcp/agents mounts
    app.state.approval_queue = ApprovalQueue()
    app.state.memory_provider = provider
    app.state.memory_dispatcher = None
    mcp_mount.mount_mcp_servers(
        app,
        approval_queue=app.state.approval_queue,
        memory_provider=provider,
        memory_dispatcher=None,
    )
    return app


def _count_tool_builds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    built: list[str] = []
    real = tool_base.Tool.from_function.__func__  # type: ignore[attr-defined]

    def _counting(cls: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
        built.append(kwargs.get("name") or fn.__name__)
        return real(cls, fn, *args, **kwargs)

    monkeypatch.setattr(tool_base.Tool, "from_function", classmethod(_counting))
    return built


def test_second_mount_builds_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second ``create_app()`` in the same process reuses the definitions."""
    _mount(monkeypatch, provider=_Provider("warm"))  # warm the process-wide definitions

    built = _count_tool_builds(monkeypatch)
    app = _mount(monkeypatch, provider=_Provider("second"))

    assert built == [], f"mount rebuilt {len(built)} tool definitions: {built[:5]}..."
    # ...and the second app still exposes the full catalog on both mounts.
    assert set(app.state.mcp_servers) == {"hal0-admin", "hal0-memory", "hal0-agents"}
    admin_tools = app.state.mcp_servers["hal0-admin"]._tool_manager.list_tools()
    assert {t.name for t in admin_tools} == set(admin.TOOL_DESCRIPTIONS)


@pytest.mark.asyncio
async def test_injected_context_is_never_advertised(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``ctx`` parameter that leads a shared tool back to its server is
    injected by FastMCP — it must not leak into any tool's input schema."""
    app = _mount(monkeypatch, provider=_Provider("schema"))
    for server in app.state.mcp_servers.values():
        for tool in await server.list_tools():
            props = tool.inputSchema.get("properties", {})
            assert "ctx" not in props, f"{tool.name} advertises ctx"
    memory_add = next(
        t for t in await app.state.mcp_servers["hal0-memory"].list_tools() if t.name == "memory_add"
    )
    assert "text" in memory_add.inputSchema["properties"]


def test_each_app_gets_its_own_server_and_session_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared definitions, never a shared server: a session manager's
    ``run()`` is single-use, so each app's lifespan needs its own."""
    a = _mount(monkeypatch, provider=_Provider("a"))
    b = _mount(monkeypatch, provider=_Provider("b"))

    for key in ("hal0-admin", "hal0-memory", "hal0-agents"):
        assert a.state.mcp_servers[key] is not b.state.mcp_servers[key]
    assert not set(map(id, a.state.mcp_session_managers)) & set(
        map(id, b.state.mcp_session_managers)
    )


@pytest.mark.asyncio
async def test_gated_admin_call_lands_in_its_own_apps_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _mount(monkeypatch)
    b = _mount(monkeypatch)

    await a.state.mcp_servers["hal0-admin"].call_tool("model_delete", {"args": {"model_id": "m-a"}})

    assert [e["args"] for e in a.state.approval_queue.list_pending()] == [{"model_id": "m-a"}]
    assert b.state.approval_queue.list_pending() == []


@pytest.mark.asyncio
async def test_admin_tool_reads_app_state_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swapping a field on ``app.state`` after the mount takes effect —
    proof the tool looks it up per call instead of capturing it."""
    app = _mount(monkeypatch)
    replacement = ApprovalQueue()
    app.state.approval_queue = replacement

    await app.state.mcp_servers["hal0-admin"].call_tool("model_delete", {"args": {"model_id": "m"}})

    assert len(replacement.list_pending()) == 1


@pytest.mark.asyncio
async def test_admin_rest_forward_uses_its_own_apps_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _fake_rest(*, base_url: str, **_kwargs: Any) -> dict[str, Any]:
        seen.append(base_url)
        return {"status": "ok"}

    monkeypatch.setattr(admin, "_call_rest", _fake_rest)
    monkeypatch.setattr(admin, "install_admin_route_map", lambda _app: None)
    apps = []
    for url in ("http://a.test", "http://b.test"):
        app = FastAPI()
        app.state.approval_queue = ApprovalQueue()
        mcp_mount.mount_mcp_servers(app, approval_queue=app.state.approval_queue, base_url=url)
        apps.append(app)

    for app in apps:
        await app.state.mcp_servers["hal0-admin"].call_tool("profile_list", {"args": {}})

    assert seen == ["http://a.test", "http://b.test"]


@pytest.mark.asyncio
async def test_memory_tool_hits_its_own_apps_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    pa, pb = _Provider("a"), _Provider("b")
    a = _mount(monkeypatch, provider=pa)
    _mount(monkeypatch, provider=pb)

    await a.state.mcp_servers["hal0-memory"].call_tool("memory_list", {})

    assert (pa.list_calls, pb.list_calls) == (1, 0)

    # Call-time lookup on the memory mount too.
    pc = _Provider("c")
    a.state.memory_provider = pc
    await a.state.mcp_servers["hal0-memory"].call_tool("memory_list", {})
    assert (pa.list_calls, pc.list_calls) == (1, 1)


@pytest.mark.asyncio
async def test_unbound_tool_call_fails_loudly() -> None:
    """A shared definition invoked with no server context must not fall back
    to some other app's state."""
    tool = admin.build_server(approval_queue=ApprovalQueue())._tool_manager.get_tool("profile_list")
    assert tool is not None
    with pytest.raises(ToolError, match="not bound to a hal0 MCP server"):
        await tool.run({"args": {}}, context=None)


@pytest.mark.asyncio
async def test_agents_tool_reads_its_own_apps_bridge_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``hal0-agents`` definitions are shared; the bridge service is per app, read per call."""
    from starlette.requests import Request

    class _Service:
        def __init__(self, label: str) -> None:
            self.label = label

        async def list_agents(self) -> dict[str, Any]:
            return {"agents": [{"id": self.label}]}

    caller = AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"agents"}))
    request = Request({"type": "http", "headers": [], "hal0.bridge_principal": caller})
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    a = _mount(monkeypatch)
    b = _mount(monkeypatch)
    a.state.bridge_service = _Service("a")
    b.state.bridge_service = _Service("b")

    async def agent_id(app: FastAPI) -> str:
        out = await app.state.mcp_servers["hal0-agents"].call_tool("agents_list", {})
        payload = out[1] if isinstance(out, tuple) else out
        assert isinstance(payload, dict)
        return str(payload["agents"][0]["id"])

    assert [await agent_id(a), await agent_id(b)] == ["a", "b"]
    a.state.bridge_service = _Service("swapped")  # call-time lookup, not captured at mount
    assert await agent_id(a) == "swapped"
