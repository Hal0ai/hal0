"""Brain chat coverage for the platform-management admin-catalog expansion.

``hal0.brain.chat`` surfaces the hal0-admin MCP catalog dynamically —
``_surfaced_tool_schemas`` = local board/platform schemas + the admin
catalog minus ``_ADMIN_TOOL_EXCLUDES`` (see that module's "admin-MCP tool
surface" section). New admin tools therefore reach the steward chat with
zero chat.py changes; these tests pin that the combined surface actually
grew to include the new platform-management tools with no name collisions,
and that a brand-new GATED platform tool queued from the chat resumes
through the SAME ApprovalQueue -> executor path an operator resolves via
the dashboard's Approvals panel (mirrors tests/mcp/test_admin.py's
gated-executor-hits-rest-on-approve pattern, one level up the stack).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.api.routes import board_chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


def _fake_request(
    *,
    read_only: bool = False,
    persona_root: Path | None = None,
    queue: ApprovalQueue | None = None,
) -> Any:
    """A Request stand-in carrying exactly what _dispatch_tool/_surfaced_tool_schemas
    read — mirrors tests/brain/test_brain_read_only.py's helper of the same shape."""
    state = SimpleNamespace(
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=read_only)),
        approval_queue=queue if queue is not None else ApprovalQueue(),
        memory_dispatcher=None,
        self_api_base_url="http://testserver",
        brain_persona_root=persona_root or Path("/nonexistent-personas-root"),
        platform_http=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


# ── combined surfaced-schema list matches the dynamic local+admin formula ───


def test_surfaced_tool_schemas_equals_local_plus_admin_minus_excludes() -> None:
    request = _fake_request()
    surfaced_names = {s["function"]["name"] for s in bc._surfaced_tool_schemas(request)}
    local_names = {s["function"]["name"] for s in bc._tool_schemas()}
    admin_catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    expected = local_names | (admin_catalog - bc._ADMIN_TOOL_EXCLUDES)
    assert surfaced_names == expected

    # Locals + admin-minus-excludes must be genuinely disjoint (a collision
    # would still satisfy the equality check above via set union, so this
    # is the assertion that actually catches a newly-introduced clash).
    assert local_names.isdisjoint(admin_catalog - bc._ADMIN_TOOL_EXCLUDES)

    # The brain surfaces NONE of the 26 memory_* tools — it handles memory
    # through the hal0-brain persona's own namespace via Hindsight, not the
    # agent memory engine's MCP dispatcher. bc._is_admin_tool_excluded is a
    # memory_-PREFIX check (not a 26-name literal list), so this also
    # future-proofs a 27th hal0.mcp.memory tool with zero chat.py changes.
    memory_tools = {t for t in admin_catalog if t.startswith("memory_")}
    assert len(memory_tools) == 26
    assert not any(bc._is_admin_tool_excluded(t) is False for t in memory_tools)
    assert surfaced_names.isdisjoint(memory_tools)

    # The platform-management expansion actually reached the chat surface —
    # one representative tool per new domain.
    for new_tool in (
        "service_list",
        "service_action",
        "comfyui_switchover",
        "updater_state",
        "hardware_snapshot",
        "slot_pull_image",
        "model_validate",
        "bench_roster",
        "bench_run",
        "journal_snapshot",
        "approval_list",
        "runner_image_list",
        "backend_list",
        "npu_backend_load",
        "mcp_server_list",
        "mcp_server_install",
        "profile_generate",
    ):
        assert new_tool in surfaced_names, f"{new_tool} did not reach _surfaced_tool_schemas"


def test_surfaced_tool_schemas_carry_the_shared_param_schema() -> None:
    """Spot-check: a new tool's advertised schema is exactly
    admin.tool_param_schema's output — same source of truth as the MCP
    server itself (no second, drift-prone copy in chat.py)."""
    request = _fake_request()
    by_name = {s["function"]["name"]: s for s in bc._surfaced_tool_schemas(request)}
    schema = by_name["npu_backend_load"]["function"]["parameters"]
    assert schema == admin.tool_param_schema("npu_backend_load")
    assert schema["required"] == ["model_id"]


# ── end-to-end: a new gated platform tool resumes through approve() ─────────


@pytest.mark.asyncio
async def test_brain_gated_platform_tool_queues_and_resumes_on_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """npu_backend_load (new, GATED) queued from the chat's tool-call path
    lands pending, hits NO REST until approved, and the SAME approval
    queue's ``approve()`` drives the executor through to a real (mocked)
    REST call — proving the platform-management expansion's gated tools
    are wired into the chat's approval-resume path, not just statically
    classified."""
    calls: list[tuple[str, str, Any]] = []

    class _MockResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {"slot": "npu-lfm2-1-2b", "state": "loaded", "created": True}

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            self.base_url = base_url

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def post(self, url: str, json: Any = None, headers: Any = None) -> _MockResponse:
            calls.append(("POST", url, json))
            return _MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    queue = ApprovalQueue()
    request = _fake_request(read_only=False, queue=queue)

    # Same injection point the LLM tool-call loop drives (_dispatch_tool ->
    # falls through to _dispatch_admin_tool for a non-local, non-excluded
    # admin-catalog name).
    result = await bc._dispatch_tool(
        request, None, "npu_backend_load", {"model_id": "lfm2:1.2b"}, board=None
    )
    assert result["status"] == "pending_approval"
    assert isinstance(result["approval_id"], str)
    assert calls == []  # nothing reached REST while pending
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "npu_backend_load"

    # Operator approves (same call the dashboard's Approvals panel makes).
    approved = await queue.approve(result["approval_id"])
    assert approved["state"] == "executed"
    assert approved["result"] == {"slot": "npu-lfm2-1-2b", "state": "loaded", "created": True}

    method, url, payload = calls[-1]
    assert method == "POST"
    assert url == "http://testserver/api/backends/npu/load"
    assert payload == {"model_id": "lfm2:1.2b"}


@pytest.mark.asyncio
async def test_brain_read_only_still_refuses_new_gated_platform_tool() -> None:
    """The KB-2/3 read-only guardrail covers the expansion too — a new
    gated tool is refused before it ever reaches the approval queue,
    same posture as the pre-existing admin-gated tools in
    test_brain_read_only.py."""
    queue = ApprovalQueue()
    request = _fake_request(read_only=True, queue=queue)
    result = await bc._dispatch_tool(
        request, None, "npu_backend_load", {"model_id": "lfm2:1.2b"}, board=None
    )
    assert "read-only mode" in result["error"]
    assert queue.list_pending() == []
