"""The sidebar Brain's admin-MCP tool surface (board_chat ↔ hal0.mcp.admin).

The slide-out chat surfaces the hal0-admin catalog as OpenAI tool schemas
and routes those calls through the same ``admin.dispatch`` core as the
/mcp/admin mount — same classification, same ApprovalQueue, same audit.
These tests pin the wiring: catalog surfaced minus exclusions, locals win
on collision, gated tools come back ``pending_approval`` from the chat
path, and the routing degrades to a typed error without app wiring.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.agents.personas import Persona, PersonaApproval, save_persona
from hal0.api.routes import board_chat as bc
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


def _fake_request(**state: Any) -> Any:
    """A Request stand-in exposing exactly what the admin path reads.

    ``brain_persona_root`` defaults to a nonexistent dir so the policy
    loader resolves to None — never the test box's live persona store.
    """
    defaults: dict[str, Any] = {
        "approval_queue": None,
        "memory_dispatcher": None,
        "self_api_base_url": "http://testserver",
        "brain_persona_root": Path("/nonexistent-personas-root"),
    }
    defaults.update(state)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**defaults)), headers={})


def _brain_persona_root(tmp_path: Path, **overrides: Any) -> Path:
    """Write a hal0-brain persona TOML into ``tmp_path`` and return it."""
    approval = overrides.pop("approval", PersonaApproval())
    persona = Persona(
        id=bc.BRAIN_PERSONA_ID,
        display_name="hal0 Brain",
        approval=approval,
        **overrides,
    )
    save_persona(persona, root=tmp_path)
    return tmp_path


# ── surfaced schema shape ────────────────────────────────────────────────────


def test_admin_schemas_surface_catalog_minus_exclusions() -> None:
    names = {s["function"]["name"] for s in bc._admin_tool_schemas()}
    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    assert names == catalog - bc._ADMIN_TOOL_EXCLUDES
    # The Brain's platform hands are actually in there.
    for expected in ("model_pull", "profile_create", "stack_apply", "slot_edit", "model_store"):
        assert expected in names


def test_admin_schemas_do_not_collide_with_local_tools() -> None:
    """One tool name = one schema across the combined list the LLM sees."""
    local = [s["function"]["name"] for s in bc._tool_schemas()]
    combined = local + [s["function"]["name"] for s in bc._admin_tool_schemas()]
    assert len(combined) == len(set(combined)), "duplicate tool names in the LLM tool list"


def test_admin_schemas_require_path_args() -> None:
    """Path args (slot name, model id…) surface as required string props."""
    by_name = {s["function"]["name"]: s["function"]["parameters"] for s in bc._admin_tool_schemas()}
    assert by_name["model_pull"]["required"] == ["model_id"]
    assert by_name["model_pull"]["properties"]["model_id"] == {"type": "string"}
    # Body/query fields stay open for description-driven args (hf_repo etc.).
    assert by_name["model_pull"]["additionalProperties"] is True
    assert by_name["profile_list"]["required"] == []


def test_excluded_memory_tools_not_surfaced() -> None:
    names = {s["function"]["name"] for s in bc._admin_tool_schemas()}
    assert not any(n.startswith("memory_") for n in names)


# ── dispatch routing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_tool_routes_admin_names_through_mcp_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin-catalog name that no local resolver claims lands in
    admin.dispatch with the persona as client_id."""
    seen: dict[str, Any] = {}

    async def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(admin, "dispatch", _fake_dispatch)
    request = _fake_request(approval_queue=ApprovalQueue())
    result = await bc._dispatch_tool(request, client=None, name="profile_list", args={}, board=None)
    assert result == {"ok": True}
    assert seen["tool"] == "profile_list"
    assert seen["client_id"] == bc.BRAIN_PERSONA_ID
    assert seen["base_url"] == "http://testserver"


@pytest.mark.asyncio
async def test_unknown_tool_still_errors() -> None:
    request = _fake_request(approval_queue=ApprovalQueue())
    result = await bc._dispatch_tool(request, client=None, name="not_a_tool", args={}, board=None)
    assert result == {"error": "unknown tool: not_a_tool"}


@pytest.mark.asyncio
async def test_gated_tool_returns_pending_approval_from_chat_path() -> None:
    """model_pull from the sidebar enqueues on the SAME ApprovalQueue the
    /mcp/admin mount uses — nothing executes until the operator approves."""
    queue = ApprovalQueue()
    request = _fake_request(approval_queue=queue)
    result = await bc._dispatch_admin_tool(request, "model_pull", {"model_id": "m"})
    assert result["status"] == "pending_approval"
    assert result["approval_id"]
    pending = queue.list_pending()
    assert [p["tool"] for p in pending] == ["model_pull"]
    assert pending[0]["client_id"] == bc.BRAIN_PERSONA_ID


@pytest.mark.asyncio
async def test_admin_dispatch_degrades_without_approval_queue() -> None:
    """No lifespan wiring (approval_queue=None) → typed error, no crash."""
    request = _fake_request(approval_queue=None)
    result = await bc._dispatch_admin_tool(request, "model_pull", {"model_id": "m"})
    assert "unavailable" in result["error"]


# ── persona ToolPolicy enforcement on the sidebar surface ────────────────────


def test_brain_policy_none_when_persona_missing(tmp_path: Path) -> None:
    request = _fake_request(brain_persona_root=tmp_path)  # empty store
    assert bc._brain_tool_policy(request) is None


def test_brain_policy_loads_from_persona_toml(tmp_path: Path) -> None:
    root = _brain_persona_root(
        tmp_path,
        approval=PersonaApproval(auto_approve=("model_pull",), require_approval=("slot_load",)),
    )
    policy = bc._brain_tool_policy(_fake_request(brain_persona_root=root))
    assert policy is not None
    assert policy.classify("model_pull", server_gated=True) == "run"
    assert policy.classify("slot_load", server_gated=False) == "gated"


def test_surfaced_schemas_filtered_by_tools_allowed(tmp_path: Path) -> None:
    """tools_allowed narrows BOTH the local and admin schema lists."""
    root = _brain_persona_root(tmp_path, tools_allowed=("get_board", "profile_*"))
    request = _fake_request(brain_persona_root=root)
    names = {s["function"]["name"] for s in bc._surfaced_tool_schemas(request)}
    assert "get_board" in names
    assert "profile_list" in names and "profile_create" in names
    assert "model_pull" not in names  # admin tool hidden
    assert "slot_load" not in names  # local tool hidden
    # No persona → everything surfaces.
    full = {s["function"]["name"] for s in bc._surfaced_tool_schemas(_fake_request())}
    assert "model_pull" in full and "slot_load" in full


@pytest.mark.asyncio
async def test_dispatch_tool_refuses_disallowed_local_tool(tmp_path: Path) -> None:
    root = _brain_persona_root(tmp_path, tools_allowed=("get_board",))
    request = _fake_request(brain_persona_root=root, approval_queue=ApprovalQueue())
    result = await bc._dispatch_tool(request, client=None, name="slot_load", args={}, board=None)
    assert "tools_allowed" in result["error"]


@pytest.mark.asyncio
async def test_persona_loosening_skips_approval_from_sidebar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_approve=model_pull in the persona TOML → the sidebar call
    executes immediately instead of queueing."""
    executed: dict[str, Any] = {}

    async def _fake_execute(**kwargs: Any) -> dict[str, Any]:
        executed.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(admin, "_execute_tool", _fake_execute)
    root = _brain_persona_root(tmp_path, approval=PersonaApproval(auto_approve=("model_pull",)))
    queue = ApprovalQueue()
    request = _fake_request(brain_persona_root=root, approval_queue=queue)
    result = await bc._dispatch_admin_tool(request, "model_pull", {"model_id": "m"})
    assert result == {"ok": True}
    assert executed["tool"] == "model_pull"
    assert queue.list_pending() == []


@pytest.mark.asyncio
async def test_persona_cannot_loosen_destructive_floor(tmp_path: Path) -> None:
    root = _brain_persona_root(tmp_path, approval=PersonaApproval(auto_approve=("model_delete",)))
    queue = ApprovalQueue()
    request = _fake_request(brain_persona_root=root, approval_queue=queue)
    result = await bc._dispatch_admin_tool(request, "model_delete", {"model_id": "m"})
    assert result["status"] == "pending_approval"
    assert [p["tool"] for p in queue.list_pending()] == ["model_delete"]
