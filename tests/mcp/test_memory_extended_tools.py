"""MCP dispatcher tests for the Hindsight-parity memory tools:

reflect, curate/history, mental models, directives, operations, tags/stats,
consolidate. Mirrors the arg-validation + dispatch style of test_memory.py —
a minimal fake wrapper recording every call, one test per behavior pinned.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.mcp import memory


class _FakeWrapper:
    """Records every capability-surface call; canned happy-path returns."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, _tool: str, **kwargs: Any) -> None:
        self.calls.append((_tool, kwargs))

    def _last(self, name: str) -> dict[str, Any]:
        for n, kwargs in reversed(self.calls):
            if n == name:
                return kwargs
        raise AssertionError(f"{name} was never called")

    async def reflect(self, **kwargs: Any) -> dict[str, Any]:
        self._record("reflect", **kwargs)
        return {"text": "an answer"}

    async def curate(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("curate", memory_id=memory_id, **kwargs)
        return {"id": memory_id}

    async def memory_history(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("memory_history", memory_id=memory_id, **kwargs)
        return {"items": []}

    async def list_mental_models(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_mental_models", **kwargs)
        return {"items": []}

    async def get_mental_model(self, mm_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("get_mental_model", mm_id=mm_id, **kwargs)
        return {"id": mm_id}

    async def create_mental_model(self, **kwargs: Any) -> dict[str, Any]:
        self._record("create_mental_model", **kwargs)
        return {"mental_model_id": "mm-1", "operation_id": "op-1"}

    async def update_mental_model(self, mm_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("update_mental_model", mm_id=mm_id, **kwargs)
        return {"id": mm_id}

    async def delete_mental_model(self, mm_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_mental_model", mm_id=mm_id, **kwargs)
        return {"success": True}

    async def refresh_mental_model(self, mm_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("refresh_mental_model", mm_id=mm_id, **kwargs)
        return {"operation_id": "op-2", "status": "queued"}

    async def list_directives(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_directives", **kwargs)
        return {"items": []}

    async def get_directive(self, d_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("get_directive", d_id=d_id, **kwargs)
        return {"id": d_id}

    async def create_directive(self, **kwargs: Any) -> dict[str, Any]:
        self._record("create_directive", **kwargs)
        return {"id": "dir-1"}

    async def update_directive(self, d_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("update_directive", d_id=d_id, **kwargs)
        return {"id": d_id}

    async def delete_directive(self, d_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_directive", d_id=d_id, **kwargs)
        return {"success": True}

    async def list_operations(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_operations", **kwargs)
        return {"operations": []}

    async def get_operation(self, op_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("get_operation", op_id=op_id, **kwargs)
        return {"operation_id": op_id, "status": "completed"}

    async def cancel_operation(self, op_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("cancel_operation", op_id=op_id, **kwargs)
        return {"success": True}

    async def retry_operation(self, op_id: str, **kwargs: Any) -> dict[str, Any]:
        self._record("retry_operation", op_id=op_id, **kwargs)
        return {"success": True}

    async def list_tags(self, **kwargs: Any) -> dict[str, Any]:
        self._record("list_tags", **kwargs)
        return {"items": [], "total": 0}

    async def bank_stats(self, **kwargs: Any) -> dict[str, Any]:
        self._record("bank_stats", **kwargs)
        return {"bank_id": "shared"}

    async def consolidate(self, **kwargs: Any) -> dict[str, Any]:
        self._record("consolidate", **kwargs)
        return {"operation_id": "op-3", "deduplicated": False}


@pytest.fixture
def wrapper() -> _FakeWrapper:
    return _FakeWrapper()


@pytest.fixture
def dispatcher(wrapper: _FakeWrapper):
    return memory.make_dispatcher(
        wrapper, client_id_resolver=lambda: "pi-coder", private_resolver=lambda: False
    )


# ── reflect ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_reflect", {"query": "what do you know about X?"})
    assert out["status"] == "ok"
    assert out["text"] == "an answer"
    call = wrapper._last("reflect")
    assert call["query"] == "what do you know about X?"
    assert call["dataset"] == "shared"
    assert call["budget"] == "low"


@pytest.mark.asyncio
async def test_reflect_requires_query(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_reflect", {})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_reflect_rejects_bad_budget(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_reflect", {"query": "x", "budget": "extreme"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_reflect_unsupported_engine_surfaces_as_status(dispatcher_unused: Any = None) -> None:
    """An engine without reflect answers {status: unsupported} (ABC default)
    — the outer envelope must report that status, not clobber it with 'ok'."""

    class _NoReflectWrapper(_FakeWrapper):
        async def reflect(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "unsupported"}

    disp = memory.make_dispatcher(
        _NoReflectWrapper(), client_id_resolver=lambda: "a", private_resolver=lambda: False
    )
    out = await disp("memory_reflect", {"query": "x"})
    assert out["status"] == "unsupported"


# ── curate / history ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_curate_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher(
        "memory_curate", {"id": "fact-1", "state": "invalidated", "reason": "superseded"}
    )
    assert out["status"] == "ok"
    call = wrapper._last("curate")
    assert call["memory_id"] == "fact-1"
    assert call["state"] == "invalidated"
    assert call["reason"] == "superseded"


@pytest.mark.asyncio
async def test_curate_requires_id(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_curate", {"state": "valid"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_curate_requires_at_least_one_field(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_curate", {"id": "fact-1"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_curate_rejects_bad_state(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_curate", {"id": "fact-1", "state": "gone"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_curate_entities_must_be_strings(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    """memory_curate's entities replaces by NAME (list[str]) — a different
    shape from memory_add's entities (list[dict])."""
    out = await dispatcher("memory_curate", {"id": "fact-1", "entities": [{"text": "Alice"}]})
    assert out["status"] == "error"
    out_ok = await dispatcher("memory_curate", {"id": "fact-1", "entities": ["Alice", "Bob"]})
    assert out_ok["status"] == "ok"


@pytest.mark.asyncio
async def test_history_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_history", {"id": "fact-1"})
    assert out["status"] == "ok"
    assert wrapper._last("memory_history")["memory_id"] == "fact-1"


# ── mental models ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mental_model_create_requires_name_and_source_query(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    out = await dispatcher("memory_mental_model_create", {"name": "x"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_mental_model_create_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher(
        "memory_mental_model_create", {"name": "Prefs", "source_query": "What does X prefer?"}
    )
    assert out["status"] == "ok"
    assert out["mental_model_id"] == "mm-1"


@pytest.mark.asyncio
async def test_mental_model_create_max_tokens_bounds(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    out = await dispatcher(
        "memory_mental_model_create",
        {"name": "x", "source_query": "y", "max_tokens": 100},
    )
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_mental_model_delete_gates_through_approval(wrapper: _FakeWrapper) -> None:
    from hal0.mcp.approval_queue import ApprovalQueue

    queue = ApprovalQueue()
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await disp("memory_mental_model_delete", {"id": "mm-1"})
    assert out["status"] == "pending_approval"
    assert not wrapper.calls  # nothing ran yet

    result = await queue.approve(out["approval_id"])
    assert result["result"]["success"] is True
    assert wrapper._last("delete_mental_model")["mm_id"] == "mm-1"


@pytest.mark.asyncio
async def test_mental_model_refresh_not_gated(wrapper: _FakeWrapper) -> None:
    """Only delete is destructive here — refresh mutates content but stays
    autonomous, same posture as memory_add."""
    from hal0.mcp.approval_queue import ApprovalQueue

    queue = ApprovalQueue()
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await disp("memory_mental_model_refresh", {"id": "mm-1"})
    assert out["status"] == "ok"
    assert queue.list_pending() == []


# ── directives ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_directive_create_requires_name_and_content(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    out = await dispatcher("memory_directive_create", {"name": "x"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_directive_create_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher(
        "memory_directive_create", {"name": "Be terse", "content": "Prefer short answers."}
    )
    assert out["status"] == "ok"
    assert out["id"] == "dir-1"
    call = wrapper._last("create_directive")
    assert call["is_active"] is True  # default
    assert call["priority"] == 0  # default


@pytest.mark.asyncio
async def test_directive_delete_gates_through_approval(wrapper: _FakeWrapper) -> None:
    from hal0.mcp.approval_queue import ApprovalQueue

    queue = ApprovalQueue()
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await disp("memory_directive_delete", {"id": "dir-1"})
    assert out["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_directive_list_normalises_csv_tags(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    await dispatcher("memory_directive_list", {"tags": "a, b"})
    assert wrapper._last("list_directives")["tags"] == ["a", "b"]


# ── operations ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operation_list_rejects_bad_status(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_operation_list", {"status": "wat"})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_operation_get_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_operation_get", {"id": "op-1"})
    assert out["status"] == "ok"
    assert out["operation_id"] == "op-1"


@pytest.mark.asyncio
async def test_operation_cancel_not_gated(wrapper: _FakeWrapper) -> None:
    """Cancel aborts in-flight work but deletes no durable memory — it must
    NOT gate like memory_delete."""
    from hal0.mcp.approval_queue import ApprovalQueue

    queue = ApprovalQueue()
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await disp("memory_operation_cancel", {"id": "op-1"})
    assert out["status"] == "ok"
    assert queue.list_pending() == []


@pytest.mark.asyncio
async def test_operation_retry_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_operation_retry", {"id": "op-1"})
    assert out["status"] == "ok"


# ── tags / bank stats / consolidate ──────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_list_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_tags_list", {})
    assert out["status"] == "ok"
    assert wrapper._last("list_tags")["dataset"] == "shared"


@pytest.mark.asyncio
async def test_bank_stats_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_bank_stats", {"dataset": "agents"})
    assert out["status"] == "ok"
    assert wrapper._last("bank_stats")["dataset"] == "agents"


@pytest.mark.asyncio
async def test_bank_consolidate_not_gated(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_bank_consolidate", {})
    assert out["status"] == "ok"
    assert out["operation_id"] == "op-3"


# ── all new tools reject an unaddressable namespace the same way as memory_add ─


@pytest.mark.asyncio
async def test_new_tools_reject_unknown_dataset(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    for tool, args in (
        ("memory_reflect", {"query": "x", "dataset": "not-a-real-bank"}),
        ("memory_tags_list", {"dataset": "not-a-real-bank"}),
        ("memory_bank_stats", {"dataset": "not-a-real-bank"}),
    ):
        out = await dispatcher(tool, args)
        assert out["status"] == "error", (tool, out)
        assert out["error"]["code"] == "mcp.memory_schema"


# ── standalone server: all new tools carry annotations + typed schemas ──────


@pytest.mark.asyncio
async def test_standalone_server_lists_every_new_tool(wrapper: _FakeWrapper) -> None:
    server = memory.build_server(wrapper=wrapper)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        "memory_reflect",
        "memory_curate",
        "memory_history",
        "memory_mental_model_list",
        "memory_mental_model_get",
        "memory_mental_model_create",
        "memory_mental_model_update",
        "memory_mental_model_delete",
        "memory_mental_model_refresh",
        "memory_directive_list",
        "memory_directive_get",
        "memory_directive_create",
        "memory_directive_update",
        "memory_directive_delete",
        "memory_operation_list",
        "memory_operation_get",
        "memory_operation_cancel",
        "memory_operation_retry",
        "memory_tags_list",
        "memory_bank_stats",
        "memory_bank_consolidate",
    }
    assert expected <= names
    by_name = {t.name: t for t in tools}
    for name in expected:
        ann = by_name[name].annotations
        assert ann is not None, f"{name}: annotations missing"
        assert ann.readOnlyHint is not None
        assert ann.destructiveHint is not None
    assert by_name["memory_mental_model_delete"].annotations.destructiveHint is True
    assert by_name["memory_directive_delete"].annotations.destructiveHint is True
    assert by_name["memory_bank_stats"].annotations.readOnlyHint is True
