"""Unit tests for :mod:`hal0.mcp.admin` tool catalog + dispatch.

* Tool registration — every ADR-0004 §4 tool ends up on the FastMCP
  instance with the right description.
* Autonomous read tool calls dispatch httpx with the agent's Bearer.
* Autonomous write tool runs immediately (no approval enqueue).
* Gated tool returns ``{status:"pending_approval", approval_id:...}``
  and an entry lands in the queue.
* ``memory_delete`` is gated only when ``len(ids) > 1``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch httpx.AsyncClient so REST calls are observable + no network."""

    captured: dict[str, Any] = {"calls": []}

    class _MockResponse:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload
            self.text = ""

        def json(self) -> dict[str, Any]:
            return self._payload

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("GET", url, params, dict(headers or {})))
            return _MockResponse({"ok": "get"})

        async def post(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("POST", url, json, dict(headers or {})))
            return _MockResponse({"ok": "post"})

        async def delete(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("DELETE", url, params, dict(headers or {})))
            return _MockResponse({"ok": "delete"})

        async def put(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            captured["calls"].append(("PUT", url, json, dict(headers or {})))
            return _MockResponse({"ok": "put"})

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    return captured


def test_classification_buckets_match_adr_0004() -> None:
    """Every documented ADR-0004 §4 tool lives in exactly one bucket."""
    read = admin.AUTONOMOUS_READ_TOOLS
    write = admin.AUTONOMOUS_WRITE_TOOLS
    gated = admin.GATED_TOOLS
    # No overlap.
    assert read.isdisjoint(write)
    assert read.isdisjoint(gated)
    # memory_delete is in autonomous_write (the gating branches on args).
    assert "memory_delete" in write
    # ADR-0004 §4 destructives.
    for t in (
        "model_pull",
        "model_delete",
        "slot_create",
        "slot_delete",
        "slot_restart",
        "capability_set",
        "config_write",
        "provider_credential_write",
    ):
        assert t in gated
    # `logs_tail` was promoted from autonomous-read to GATED per
    # security review MED-1 — it stays gated until the journald
    # redactor in routes/logs.py covers Bearer + X-API-Key + provider
    # keys per ADR-0004 §7.
    assert "logs_tail" in gated
    # ADR-0004 §4 reads.
    for t in (
        "slot_list",
        "slot_status",
        "model_list",
        "hardware_probe",
        "capability_list",
        "provider_list",
        "version_info",
    ):
        assert t in read


def test_is_gated_memory_delete_branches_on_id_count() -> None:
    assert admin.is_gated("memory_delete", {"ids": ["a"]}) is False
    assert admin.is_gated("memory_delete", {"ids": ["a", "b"]}) is True
    assert admin.is_gated("memory_add", {"text": "x", "dataset": "d"}) is False
    assert admin.is_gated("model_pull", {"model_id": "x"}) is True


@pytest.mark.asyncio
async def test_build_server_registers_full_catalog(queue: ApprovalQueue) -> None:
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    tools = await server.list_tools()
    registered = {t.name for t in tools}
    expected = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    assert expected.issubset(registered)


def test_every_tool_has_annotations() -> None:
    """Every tool registered with FastMCP must carry the four MCP hints
    (readOnly / destructive / idempotent / openWorld). New tools added
    to the catalog without an annotation row trip this guard."""
    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    missing = catalog - admin._ANNOTATIONS.keys()
    assert not missing, f"tools missing MCP annotations: {sorted(missing)}"
    for name, ann in admin._ANNOTATIONS.items():
        assert ann.readOnlyHint is not None, f"{name}: readOnlyHint unset"
        assert ann.destructiveHint is not None, f"{name}: destructiveHint unset"
        assert ann.idempotentHint is not None, f"{name}: idempotentHint unset"
        assert ann.openWorldHint is not None, f"{name}: openWorldHint unset"


def test_destructive_tools_match_gated_destructive_set() -> None:
    """ADR-0004 §4's "destructive" classification must match the
    destructiveHint annotations exactly. The annotation is the
    client-facing surface; ADR-0004 is the policy. They cannot drift."""
    destructive_per_adr = {"model_delete", "slot_delete", "memory_delete"}
    destructive_per_annotation = {
        name for name, ann in admin._ANNOTATIONS.items() if ann.destructiveHint
    }
    assert destructive_per_annotation == destructive_per_adr


def test_open_world_tool_is_model_pull_only() -> None:
    """model_pull is the only tool that reaches outside hal0's own
    surface (HuggingFace + upstream registries). Anything else with
    openWorldHint=True needs a deliberate ADR update."""
    open_world = {name for name, ann in admin._ANNOTATIONS.items() if ann.openWorldHint}
    assert open_world == {"model_pull"}


@pytest.mark.asyncio
async def test_registered_tools_carry_their_annotations(queue: ApprovalQueue) -> None:
    """The annotation table must actually reach FastMCP's tool list —
    not just sit in a dict no one looks at."""
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    tools = await server.list_tools()
    by_name = {t.name: t for t in tools}
    sample = "model_delete"  # destructive — easiest to spot a regression on
    assert by_name[sample].annotations is not None
    assert by_name[sample].annotations.destructiveHint is True
    assert by_name[sample].annotations.readOnlyHint is False


@pytest.mark.asyncio
async def test_autonomous_read_dispatches_get_with_bearer(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="slot_list",
        args={},
        client_id="pi",
        bearer="token-abc",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "get"}
    call = mock_transport["calls"][-1]
    method, url, _params, headers = call
    assert method == "GET"
    assert url == "http://t/api/slots"
    assert headers["Authorization"] == "Bearer token-abc"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


@pytest.mark.asyncio
async def test_autonomous_path_arg_resolution(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """slot_status carries ``name`` as a URL path arg."""
    await admin.dispatch(
        tool="slot_status",
        args={"name": "primary"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    _, url, _, _ = mock_transport["calls"][-1]
    assert url == "http://t/api/slots/primary"


@pytest.mark.asyncio
async def test_autonomous_write_runs_now_not_queued(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    """model_swap is autonomous; it goes through REST immediately."""
    result = await admin.dispatch(
        tool="model_swap",
        args={"name": "primary", "model_id": "qwen3:0.6b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": "post"}
    # Queue stays empty.
    assert queue.list_pending() == []
    # POST hits the live slot-swap route (see admin.py drift note).
    method, url, payload, _ = mock_transport["calls"][-1]
    assert method == "POST"
    assert url == "http://t/api/slots/primary/swap"
    assert payload == {"model_id": "qwen3:0.6b"}


@pytest.mark.asyncio
async def test_gated_tool_returns_pending_approval_and_enqueues(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert isinstance(result["approval_id"], str)
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "model_pull"
    # REST was NOT hit while the call sits pending.
    assert mock_transport["calls"] == []


@pytest.mark.asyncio
async def test_gated_executor_hits_rest_on_approve(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="slot_delete",
        args={"name": "scratch"},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    aid = result["approval_id"]
    await queue.approve(aid)
    method, url, _params, headers = mock_transport["calls"][-1]
    assert method == "DELETE"
    assert url == "http://t/api/slots/scratch"
    assert headers["Authorization"] == "Bearer t"


@pytest.mark.asyncio
async def test_memory_delete_single_id_autonomous(queue: ApprovalQueue) -> None:
    """Single-id memory_delete must run through the dispatcher, not enqueue."""
    dispatcher = AsyncMock(return_value={"status": "ok", "deleted": 1})
    result = await admin.dispatch(
        tool="memory_delete",
        args={"ids": ["a"]},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
        memory_dispatcher=dispatcher,
    )
    assert result == {"status": "ok", "deleted": 1}
    assert queue.list_pending() == []
    dispatcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_delete_bulk_gated(queue: ApprovalQueue) -> None:
    dispatcher = AsyncMock(return_value={"status": "ok", "deleted": 2})
    result = await admin.dispatch(
        tool="memory_delete",
        args={"ids": ["a", "b"]},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
        memory_dispatcher=dispatcher,
    )
    assert result["status"] == "pending_approval"
    assert queue.list_pending()[0]["tool"] == "memory_delete"
    dispatcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_tool_returns_typed_error(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="rm_rf_root",
        args={},
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.unknown_tool"


@pytest.mark.asyncio
async def test_missing_path_arg_returns_typed_error(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="slot_status",
        args={},  # missing 'name'
        client_id="pi",
        bearer="t",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "mcp.missing_arg"
