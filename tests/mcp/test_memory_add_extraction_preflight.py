"""The MCP ``memory_add`` tool preflights the extraction window too (#1930).

#1903 taught ``POST /api/memory/add`` to refuse a write when the extraction
slot's effective context window cannot fit Hindsight's own extraction prompt:
such a retain is accepted with a document id and then dropped by the engine
(``retain_extract_facts`` 500 "Context size has been exceeded"), or answered by
persisting prompt scaffolding as a "fact". ``/mcp/memory`` is the path agents
actually retain through, and it called the same ``wrapper.add`` with no
preflight — so on a below-floor box an MCP retain still reported success.

These pin the MCP side of the contract: the dispatcher runs an injected
``add_preflight`` before ``wrapper.add`` and surfaces its structured error;
``mount_mcp_servers`` and the admin ``MemoryDispatcher`` wire in the SAME
helper the REST route uses, resolved against the live app state.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from hal0.api import mcp_mount
from hal0.api.routes import memory as memory_routes
from hal0.api.routes.memory import MemoryExtractionCtxTooSmall
from hal0.dispatcher.memory_dispatcher import MemoryDispatcher
from hal0.mcp import memory
from hal0.mcp.approval_queue import ApprovalQueue
from tests.api.test_memory_extraction_ctx_preflight import (
    StubHindsightWrapper,
    _FakeModelRegistry,
    _FakeSlotManager,
    _RecordingUpstreams,
)


def _below_floor() -> MemoryExtractionCtxTooSmall:
    return MemoryExtractionCtxTooSmall(
        "memory extraction slot 'utility' is below the floor",
        details={"slot": "utility", "effective_context": 4096, "required_context": 8192},
    )


@pytest.mark.asyncio
async def test_memory_add_refused_when_the_preflight_refuses() -> None:
    wrapper = StubHindsightWrapper()

    async def _preflight() -> None:
        raise _below_floor()

    dispatch = memory.make_dispatcher(
        wrapper, client_id_resolver=lambda: "hermes", add_preflight=_preflight
    )
    out = await dispatch("memory_add", {"text": "hello"})

    assert out["status"] == "error"
    assert out["error"]["code"] == "memory.extraction_ctx_too_small"
    assert out["error"]["details"]["effective_context"] == 4096
    assert wrapper.add_calls == [], "no document id may be minted for a doomed write"


@pytest.mark.asyncio
async def test_memory_add_proceeds_when_the_preflight_passes() -> None:
    wrapper = StubHindsightWrapper()
    ran: list[bool] = []

    async def _preflight() -> None:
        ran.append(True)

    dispatch = memory.make_dispatcher(
        wrapper, client_id_resolver=lambda: "hermes", add_preflight=_preflight
    )
    out = await dispatch("memory_add", {"text": "hello"})

    assert out["status"] == "ok"
    assert ran == [True]
    assert len(wrapper.add_calls) == 1


@pytest.mark.asyncio
async def test_schema_errors_win_over_the_preflight() -> None:
    """Same order as the REST route: a malformed call is told what is wrong
    with it, not that the box's extraction slot is undersized."""

    async def _preflight() -> None:
        raise _below_floor()

    dispatch = memory.make_dispatcher(StubHindsightWrapper(), add_preflight=_preflight)
    out = await dispatch("memory_add", {"text": ""})

    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_preflight_only_gates_memory_add() -> None:
    class _Wrapper(StubHindsightWrapper):
        async def search(self, **_kw: Any) -> list[Any]:
            return []

    async def _preflight() -> None:
        raise _below_floor()

    dispatch = memory.make_dispatcher(_Wrapper(), add_preflight=_preflight)
    out = await dispatch("memory_search", {"query": "x"})

    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_admin_memory_dispatcher_carries_the_preflight() -> None:
    wrapper = StubHindsightWrapper()

    async def _preflight() -> None:
        raise _below_floor()

    disp = MemoryDispatcher(wrapper, add_preflight=_preflight)
    out = await disp("memory_add", {"text": "hello"})

    assert out["error"]["code"] == "memory.extraction_ctx_too_small"
    assert wrapper.add_calls == []


def _mount_below_floor(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], Any]:
    from hal0.mcp import admin as admin_mod

    monkeypatch.setattr(admin_mod, "install_admin_route_map", lambda _app: None)
    built: dict[str, Any] = {}
    real_build = memory.build_server

    def _spy(**kwargs: Any) -> Any:
        built.update(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(memory, "build_server", _spy)

    wrapper = StubHindsightWrapper()
    app = FastAPI()
    app.state.slot_manager = _FakeSlotManager(4096)
    app.state.model_registry = _FakeModelRegistry(4096)
    app.state.upstreams = _RecordingUpstreams()
    app.state.upstream_models = {}
    mcp_mount.mount_mcp_servers(
        app, approval_queue=ApprovalQueue(), memory_provider=wrapper, memory_dispatcher=None
    )
    return built, wrapper


@pytest.mark.asyncio
async def test_mounted_memory_server_preflights_against_live_app_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real seam: the ``/mcp/memory`` server gets the REST route's own
    preflight, resolving the extraction slot from the app's slot state — a
    4096-token ``utility`` slot is refused exactly as ``/api/memory/add``
    refuses it."""
    built, wrapper = _mount_below_floor(monkeypatch)
    preflight = built.get("add_preflight")
    assert preflight is not None, "mount_mcp_servers must arm the memory_add preflight"

    with pytest.raises(MemoryExtractionCtxTooSmall):
        await preflight()

    dispatch = memory.make_dispatcher(wrapper, add_preflight=preflight)
    out = await dispatch("memory_add", {"text": "hello"})
    assert out["error"]["code"] == "memory.extraction_ctx_too_small"
    assert wrapper.add_calls == []


@pytest.mark.asyncio
async def test_rest_and_mcp_share_one_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """One owner for the rule: the MCP preflight goes through the same
    ``_extraction_window`` the REST route does."""
    seen: list[Any] = []

    async def _fake_window(request: Any, wrapper: Any) -> None:
        seen.append(wrapper)
        return None

    monkeypatch.setattr(memory_routes, "_extraction_window", _fake_window)
    built, wrapper = _mount_below_floor(monkeypatch)

    await built["add_preflight"]()

    assert seen == [wrapper]
