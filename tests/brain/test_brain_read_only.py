"""Read-only-default posture for the hal0-brain steward chat (KB-2/3 §4).

The ``[brain_chat] read_only`` guardrail keeps the steward answering and
reading state while refusing EVERY mutating and admin-write tool, enforced in
``_dispatch_tool`` independently of the persona's ``tools_allowed`` / approval
policy. These tests pin:

  * reads pass through under read-only (board reads, platform reads, admin
    autonomous-reads);
  * every non-read tier (board mutation, platform slot mutation, admin
    autonomous-write, admin gated) is refused with the STABLE error surface;
  * ``_is_read_tool`` classification is correct and FAILS CLOSED on unknown
    tools;
  * the guardrail holds even when the persona would otherwise allow / loosen
    the tool.

Drives ``_dispatch_tool`` directly (the injection point the board suite uses)
so no live LLM is involved. See docs/rework/hal0-specs/spec-kb23-brain-tools.md.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.agents.personas import Persona, PersonaApproval, save_persona
from hal0.api.routes import board_chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.mcp.approval_queue import ApprovalQueue

# The stable operator-facing refusal fragment; downstream must not drift it.
_READ_ONLY_MARKER = "read-only mode"
_READ_ONLY_CONFIG_HINT = "[brain_chat] read_only=true"


class _RecordingClient:
    """A hermes_kanban stand-in: records every request_json call, returns {}."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        return {"columns": []} if path == "/board" else {"ok": True}


def _fake_request(
    *,
    read_only: bool,
    persona_root: Path | None = None,
    queue: ApprovalQueue | None = None,
    platform_http: Any = None,
) -> Any:
    """A Request stand-in carrying exactly what ``_dispatch_tool`` reads."""
    state = SimpleNamespace(
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=read_only)),
        approval_queue=queue if queue is not None else ApprovalQueue(),
        memory_dispatcher=None,
        self_api_base_url="http://testserver",
        brain_persona_root=persona_root or Path("/nonexistent-personas-root"),
        platform_http=platform_http,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


# ── the default value ships False today; the flip is a cross-lane deviation ──


def test_schema_default_documented_and_enforceable() -> None:
    """The pydantic default currently ships False (KB-2/3 §4b deviation), but
    the guardrail is fully enforceable once set True — proven by every test
    below that constructs BrainChatConfig(read_only=True)."""
    assert BrainChatConfig().read_only is False  # tracked flip; see spec §4b
    assert BrainChatConfig(read_only=True).read_only is True


# ── reads always pass under read-only ────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_only_allows_board_read() -> None:
    client = _RecordingClient()
    request = _fake_request(read_only=True)
    result = await bc._dispatch_tool(request, client, "get_board", {}, board=None)
    assert client.calls == [("GET", "/board")]
    assert _READ_ONLY_MARKER not in str(result)


@pytest.mark.asyncio
async def test_read_only_allows_admin_autonomous_read() -> None:
    """An admin autonomous-read (profile_list) is read-safe under read-only."""
    request = _fake_request(read_only=True)
    # It must be classified a read and NOT refused before dispatch.
    assert bc._is_read_tool("profile_list", {}) is True


# ── every non-read tier is refused with the stable surface ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("move_task", {"task_id": "t1", "status": "done"}),  # board mutation
        ("create_task", {"title": "x"}),  # board mutation
        ("update_orchestration", {"auto_decompose": True}),  # board write (PUT)
        ("slot_load", {"name": "ops"}),  # platform slot mutation
        ("slot_restart", {"name": "ops"}),  # platform slot mutation
        ("model_edit", {"model_id": "m"}),  # admin autonomous-write
        ("model_pull", {"model_id": "m"}),  # admin gated
        ("slot_delete", {"name": "ops"}),  # admin gated (destructive)
    ],
)
async def test_read_only_refuses_every_mutation(name: str, args: dict[str, Any]) -> None:
    client = _RecordingClient()
    request = _fake_request(read_only=True)
    result = await bc._dispatch_tool(request, client, name, args, board=None)
    assert _READ_ONLY_MARKER in result["error"]
    assert _READ_ONLY_CONFIG_HINT in result["error"]
    assert repr(name) in result["error"]
    # Nothing reached the board backend.
    assert client.calls == []


@pytest.mark.asyncio
async def test_read_only_refuses_gated_before_enqueue() -> None:
    """A gated tool is refused by read-only BEFORE it can enqueue — the
    approval queue stays empty, proving read-only wins over the gate."""
    queue = ApprovalQueue()
    request = _fake_request(read_only=True, queue=queue)
    result = await bc._dispatch_tool(request, None, "model_delete", {"model_id": "m"}, board=None)
    assert _READ_ONLY_MARKER in result["error"]
    assert queue.list_pending() == []


# ── read-only wins over a permissive persona ─────────────────────────────────


@pytest.mark.asyncio
async def test_read_only_overrides_persona_auto_approve(tmp_path: Path) -> None:
    """Even a persona that auto-approves model_pull cannot beat read-only."""
    persona = Persona(
        id=bc.BRAIN_PERSONA_ID,
        display_name="hal0 Brain",
        approval=PersonaApproval(auto_approve=("model_pull",)),
    )
    save_persona(persona, root=tmp_path)
    request = _fake_request(read_only=True, persona_root=tmp_path, queue=ApprovalQueue())
    result = await bc._dispatch_tool(request, None, "model_pull", {"model_id": "m"}, board=None)
    assert _READ_ONLY_MARKER in result["error"]


# ── _is_read_tool classification + fail-closed ───────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["get_board", "get_task", "get_assignees", "get_orchestration", "list_slots", "get_slot",
     "list_models", "hardware_stats", "list_agents", "profile_list", "settings_get", "model_show"],
)
def test_is_read_tool_true_for_reads(name: str) -> None:
    assert bc._is_read_tool(name, {"task_id": "t", "name": "n"}) is True


@pytest.mark.parametrize(
    "name",
    ["move_task", "create_task", "update_orchestration", "slot_load", "slot_unload",
     "slot_restart", "model_edit", "model_pull", "slot_delete", "config_write"],
)
def test_is_read_tool_false_for_mutations(name: str) -> None:
    assert bc._is_read_tool(name, {"task_id": "t", "name": "n", "model_id": "m"}) is False


def test_is_read_tool_fails_closed_on_unknown() -> None:
    """An unrecognised tool is NOT a read — read-only refuses it."""
    assert bc._is_read_tool("totally_made_up_tool", {}) is False


# ── read-only off: mutations pass the guardrail (sanity that it is a gate) ────


@pytest.mark.asyncio
async def test_read_only_false_allows_board_mutation() -> None:
    client = _RecordingClient()
    request = _fake_request(read_only=False)
    result = await bc._dispatch_tool(
        request, client, "move_task", {"task_id": "t1", "status": "done"}, board=None
    )
    assert "error" not in result or _READ_ONLY_MARKER not in str(result)
    assert ("PATCH", "/tasks/t1") in client.calls
