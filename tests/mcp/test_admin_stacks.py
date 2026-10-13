"""MCP admin coverage for the Stacks tools (PR-4).

The stack tools are REST passthroughs like the rest of the catalog: reads run
autonomously and forward GET; apply/import/delete gate for owner approval. These
tests assert the classification + URL routing without touching the network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"calls": []}

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"ok": True}

    class _Client:
        def __init__(self, *, base_url: str, timeout: float, **_: Any) -> None:
            captured["base_url"] = base_url

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _Resp:
            captured["calls"].append(("GET", url, params))
            return _Resp()

        async def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
            captured["calls"].append(("POST", url, json))
            return _Resp()

        async def delete(self, url: str, params: Any = None, headers: Any = None) -> _Resp:
            captured["calls"].append(("DELETE", url, params))
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return captured


def test_stack_tools_classification() -> None:
    assert {"stack_list", "stack_status"} <= admin.AUTONOMOUS_READ_TOOLS
    assert {"stack_apply", "stack_import", "stack_delete"} <= admin.GATED_TOOLS
    # Reads are not gated; writes are.
    assert admin.is_gated("stack_list", {}) is False
    assert admin.is_gated("stack_status", {"slug": "x"}) is False
    assert admin.is_gated("stack_apply", {"slug": "x"}) is True
    assert admin.is_gated("stack_import", {"slug": "x"}) is True
    assert admin.is_gated("stack_delete", {"slug": "x"}) is True


@pytest.mark.asyncio
async def test_stack_list_dispatches_get(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="stack_list",
        args={},
        client_id="pi",
        bearer="tok",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result == {"ok": True}
    method, url, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/stacks")


@pytest.mark.asyncio
async def test_stack_status_substitutes_slug(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    await admin.dispatch(
        tool="stack_status",
        args={"slug": "coding"},
        client_id="pi",
        bearer="tok",
        base_url="http://t",
        approval_queue=queue,
    )
    method, url, _ = mock_transport["calls"][-1]
    assert (method, url) == ("GET", "http://t/api/stacks/coding")


@pytest.mark.asyncio
async def test_stack_apply_gates_for_approval(queue: ApprovalQueue) -> None:
    result = await admin.dispatch(
        tool="stack_apply",
        args={"slug": "coding"},
        client_id="pi",
        bearer="tok",
        base_url="http://t",
        approval_queue=queue,
    )
    assert result["status"] == "pending_approval"
    assert "approval_id" in result


@pytest.mark.asyncio
async def test_approved_stack_apply_posts_to_apply_url(
    queue: ApprovalQueue, mock_transport: dict[str, Any]
) -> None:
    result = await admin.dispatch(
        tool="stack_apply",
        args={"slug": "coding"},
        client_id="pi",
        bearer="tok",
        base_url="http://t",
        approval_queue=queue,
    )
    # Approve → the bound executor fires the REST call.
    await queue.approve(result["approval_id"])
    method, url, _ = mock_transport["calls"][-1]
    assert (method, url) == ("POST", "http://t/api/stacks/coding/apply")
