"""Unit tests for :mod:`hal0.mcp.memory` schema + dispatcher.

Covers each tool's arg validation and return shape per ADR-0005 §2,
plus the ``--private`` toggle namespace promotion rule from §3 and
the server-injected ``source`` rule from §5.

The :class:`_FakeWrapper` stands in for the Memory-engine team's
``CogneeWrapper``. Its method signatures match the contract baked
into :mod:`hal0.mcp.memory`'s docstring.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.mcp import memory


class _FakeWrapper:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self._counter = 0

    async def add(
        self,
        *,
        text: str,
        dataset: str,
        tags: list[str],
        source: str,
        metadata: dict[str, Any],
        client_id: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        self.add_calls.append(
            {
                "text": text,
                "dataset": dataset,
                "tags": tags,
                "source": source,
                "metadata": metadata,
                "client_id": client_id,
                "document_id": document_id,
            }
        )
        self._counter += 1
        return {"id": document_id or f"id-{self._counter}", "timestamp": "2026-05-22T00:00:00Z"}

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        return [{"id": "id-1", "text": "hi", "score": 0.9}]

    async def list_items(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"items": [{"id": "id-1"}], "next_cursor": None}

    async def delete(
        self,
        *,
        ids: list[str],
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, Any]:
        self.delete_calls.append({"ids": ids, "client_id": client_id, "dataset": dataset})
        return {"deleted": len(ids)}


@pytest.fixture
def wrapper() -> _FakeWrapper:
    return _FakeWrapper()


@pytest.fixture
def dispatcher(wrapper: _FakeWrapper):
    return memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
    )


@pytest.mark.asyncio
async def test_memory_add_happy_path(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_add", {"text": "hello world"})
    assert out["status"] == "ok"
    assert out["id"] == "id-1"
    assert out["timestamp"] == "2026-05-22T00:00:00Z"
    call = wrapper.add_calls[0]
    assert call["text"] == "hello world"
    assert call["dataset"] == "shared"  # ADR-0005 §3 default
    assert call["source"] == "pi-coder"  # server-injected per §5
    assert call["tags"] == []
    assert call["metadata"] == {}


@pytest.mark.asyncio
async def test_memory_add_rejects_caller_supplied_source(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    """Per ADR-0005 §5, source is server-injected and clients cannot lie."""
    out = await dispatcher("memory_add", {"text": "x", "source": "fake-agent"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_memory_add_private_namespace_promotion(wrapper: _FakeWrapper) -> None:
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: True,
    )
    await disp("memory_add", {"text": "secret"})
    assert wrapper.add_calls[0]["dataset"] == "private:pi-coder"


@pytest.mark.asyncio
async def test_memory_add_requires_text(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_add", {})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_memory_add_empty_text_rejected(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_add", {"text": "   "})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_memory_search_returns_results_list(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_search", {"query": "cats"})
    assert out["status"] == "ok"
    assert out["results"] == [{"id": "id-1", "text": "hi", "score": 0.9}]
    call = wrapper.search_calls[0]
    # Default limit is 10 per ADR-0005 §2.
    assert call["limit"] == 10
    # Non-private mode reads ``shared`` only.
    assert call["dataset"] == "shared"


@pytest.mark.asyncio
async def test_memory_search_private_mode_reads_both_datasets(
    wrapper: _FakeWrapper,
) -> None:
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: True,
    )
    await disp("memory_search", {"query": "cats"})
    assert wrapper.search_calls[0]["dataset"] == ["shared", "private:pi-coder"]


@pytest.mark.asyncio
async def test_memory_search_accepts_dataset_list(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    await dispatcher("memory_search", {"query": "x", "dataset": ["a", "b"]})
    assert wrapper.search_calls[0]["dataset"] == ["a", "b"]


@pytest.mark.asyncio
async def test_memory_search_limit_bounds(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_search", {"query": "x", "limit": 0})
    assert out["status"] == "error"
    out = await dispatcher("memory_search", {"query": "x", "limit": 999})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_memory_list_uses_shared_default(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_list", {})
    assert out["status"] == "ok"
    assert wrapper.list_calls[0]["dataset"] == "shared"
    assert wrapper.list_calls[0]["limit"] == 50


@pytest.mark.asyncio
async def test_memory_delete_returns_count(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    out = await dispatcher("memory_delete", {"ids": ["a", "b", "c"]})
    assert out["status"] == "ok"
    assert out["deleted"] == 3


@pytest.mark.asyncio
async def test_memory_delete_requires_non_empty_list(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    out = await dispatcher("memory_delete", {"ids": []})
    assert out["status"] == "error"
    out = await dispatcher("memory_delete", {})
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_unknown_tool_returns_typed_error(dispatcher: Any) -> None:
    out = await dispatcher("memory_telepathy", {"q": "x"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.unknown_memory_tool"


@pytest.mark.asyncio
async def test_tags_normalised_from_csv(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    await dispatcher("memory_add", {"text": "x", "tags": "a, b, c"})
    assert wrapper.add_calls[0]["tags"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_private_without_client_id_errors(wrapper: _FakeWrapper) -> None:
    """Promoting to private:<client_id> requires an actual client_id."""
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: None,
        private_resolver=lambda: True,
    )
    out = await disp("memory_add", {"text": "x"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.asyncio
async def test_standalone_server_tools_carry_annotations(wrapper: _FakeWrapper) -> None:
    """The standalone /mcp/memory server must surface MCP hints —
    matching what the admin server reports for the same tool names."""
    server = memory.build_server(wrapper=wrapper)
    tools = await server.list_tools()
    by_name = {t.name: t for t in tools}
    for tool_name in ("memory_add", "memory_search", "memory_list", "memory_delete"):
        ann = by_name[tool_name].annotations
        assert ann is not None, f"{tool_name}: annotations missing"
        assert ann.readOnlyHint is not None
        assert ann.destructiveHint is not None
        assert ann.idempotentHint is not None
        assert ann.openWorldHint is not None
    # memory_delete is the destructive one.
    assert by_name["memory_delete"].annotations.destructiveHint is True
    # memory_search + memory_list are reads.
    assert by_name["memory_search"].annotations.readOnlyHint is True
    assert by_name["memory_list"].annotations.readOnlyHint is True


# ── PR: MCP hardening (typed schemas, document_id, delete dataset, scrub) ───


@pytest.mark.asyncio
async def test_memory_add_document_id_passthrough(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    """A caller-supplied document_id reaches the wrapper (conversation upsert)."""
    out = await dispatcher("memory_add", {"text": "turn 2", "document_id": "conv-abc_1"})
    assert out["status"] == "ok"
    assert out["id"] == "conv-abc_1"
    assert wrapper.add_calls[0]["document_id"] == "conv-abc_1"


@pytest.mark.asyncio
async def test_memory_add_document_id_grammar_enforced(
    wrapper: _FakeWrapper, dispatcher: Any
) -> None:
    """document_id becomes an engine URL path segment — bad grammar is a schema error."""
    out = await dispatcher("memory_add", {"text": "x", "document_id": "../../etc/passwd"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_schema"
    assert not wrapper.add_calls


@pytest.mark.asyncio
async def test_memory_add_surfaces_async_operation_id(dispatcher_factory: Any = None) -> None:
    """Engines that ingest asynchronously return operation_id — it must
    survive to the caller so ingestion can be polled."""

    class _AsyncEngineWrapper(_FakeWrapper):
        async def add(self, **kwargs: Any) -> dict[str, Any]:
            self.add_calls.append(kwargs)
            return {"id": "doc-1", "timestamp": "t", "operation_id": "op-42"}

    wrapper = _AsyncEngineWrapper()
    disp = memory.make_dispatcher(wrapper, client_id_resolver=lambda: "a", private_resolver=None)
    out = await disp("memory_add", {"text": "x"})
    assert out["status"] == "ok"
    assert out["operation_id"] == "op-42"


@pytest.mark.asyncio
async def test_memory_delete_dataset_directs_sweep(wrapper: _FakeWrapper, dispatcher: Any) -> None:
    """An explicit dataset narrows/widens the engine's bank sweep (e.g.
    project items live outside the default shared+own-private sweep)."""
    out = await dispatcher("memory_delete", {"ids": ["d1"], "dataset": "project:apollo"})
    assert out["status"] == "ok"
    assert wrapper.delete_calls[0]["dataset"] == "project:apollo"


@pytest.mark.asyncio
async def test_memory_failed_error_scrubs_engine_urls() -> None:
    """httpx repeats the internal engine URL in error messages — the MCP
    envelope must not leak it to remote callers."""

    class _Resp:
        status_code = 404

    class _EngineError(Exception):
        def __init__(self) -> None:
            super().__init__(
                "Client error '404 Not Found' for url 'http://127.0.0.1:9177/v1/default/banks/x'"
            )
            self.response = _Resp()

    class _BoomWrapper(_FakeWrapper):
        async def delete(self, **kwargs: Any) -> dict[str, Any]:
            raise _EngineError()

    disp = memory.make_dispatcher(
        _BoomWrapper(), client_id_resolver=lambda: "a", private_resolver=None
    )
    out = await disp("memory_delete", {"ids": ["x"]})
    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_failed"
    assert "127.0.0.1" not in out["error"]["detail"]
    assert "9177" not in out["error"]["detail"]
    assert "404" in out["error"]["detail"]


@pytest.mark.asyncio
async def test_standalone_server_publishes_typed_schemas(wrapper: _FakeWrapper) -> None:
    """Tools must advertise real parameter schemas — the old single
    ``args: object`` param made every client guess the call shape."""
    server = memory.build_server(wrapper=wrapper)
    tools = await server.list_tools()
    props = {t.name: set((t.inputSchema or {}).get("properties", {})) for t in tools}
    assert {"text", "dataset", "tags", "metadata", "document_id"} <= props["memory_add"]
    assert {"query", "limit", "dataset", "tags", "before", "after"} <= props["memory_search"]
    assert {"ids", "dataset"} <= props["memory_delete"]
    assert {"query", "max_tokens", "types", "dataset", "tags"} <= props["memory_recall"]


@pytest.mark.asyncio
async def test_standalone_server_legacy_args_envelope_still_works(
    wrapper: _FakeWrapper,
) -> None:
    """Pre-schema clients sent {"args": {...}} — that envelope keeps working,
    with explicit params winning over same-named args keys."""
    server = memory.build_server(wrapper=wrapper)
    result = await server.call_tool("memory_add", {"args": {"text": "legacy shape"}})
    # FastMCP returns (content, structured) — the structured payload carries the envelope.
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["status"] == "ok"
    assert wrapper.add_calls[0]["text"] == "legacy shape"
