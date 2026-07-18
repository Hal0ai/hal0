"""§7.1d 🔴 regression pin: tags/capabilities routing, not slot-config labels.

Before the fix, ``active_tools_for``'s caller gate read
``"tool-calling" in caller.labels`` — a slot TOML field that nothing ever
auto-populated from the registry's per-model capability data. An operator
had to hand-author ``[model].labels = ["tool-calling"]`` in the slot TOML
in addition to whatever the registry said about the model, or the model
shipped an empty tool list despite genuinely supporting tool calls.

Post-fix, the gate reads ``LoadedSlot.tool_calling`` — sourced from the
registry model's ``capability_flags.tool_calling`` (see
``hal0.model_meta.model_capabilities_of`` +
``hal0.slots.routing.loaded_slot_from_config``) with the label check only
as a fall-through for slot TOMLs that predate the field. This test proves
a model with ``capability_flags.tool_calling=True`` and NO
``[model].labels`` at all still routes tools.
"""

from __future__ import annotations

import pytest

from hal0.omni_router.filter import active_tools_for, chat_slot_has_tool_calling
from hal0.slots.routing import loaded_slot_from_config
from tests.omni_router.conftest import FakeSlotManager, make_slot


@pytest.mark.asyncio
async def test_tool_calling_flag_alone_ships_tools_no_labels() -> None:
    """The 🔴 bug, pinned: tool_calling=True + empty labels still routes tools."""
    caller = make_slot(
        "primary",
        type="llm",
        model="agent-7b",
        labels=(),  # <-- deliberately empty: no hand-authored label mirror
        tool_calling=True,
    )
    peer = make_slot("coder", type="llm", model="qwen-coder", labels=())
    mgr = FakeSlotManager([caller, peer])
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "route_to_chat" in tools


@pytest.mark.asyncio
async def test_tool_calling_flag_false_suppresses_tools_even_with_label() -> None:
    """An explicit False flag wins over a stale ``tool-calling`` label."""
    caller = make_slot(
        "primary",
        type="llm",
        model="agent-7b",
        labels=("tool-calling",),
        tool_calling=False,
    )
    mgr = FakeSlotManager([caller])
    tools = await active_tools_for(mgr, "primary")
    assert tools == []


@pytest.mark.asyncio
async def test_legacy_labels_only_still_routes_tools() -> None:
    """Pre-migration rows (no capability_flags at all) keep working via the
    label fall-through — the routing-gate rewrite must not regress them."""
    caller = make_slot(
        "primary",
        type="llm",
        model="agent-7b",
        labels=("tool-calling",),
    )
    peer = make_slot("coder", type="llm", model="qwen-coder", labels=())
    mgr = FakeSlotManager([caller, peer])
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "route_to_chat" in tools


def test_loaded_slot_from_config_prefers_model_info_tool_calling() -> None:
    """Unit-level pin directly on the routing helper (no slot-manager stub):
    a model_info dict carrying capability_flags.tool_calling=True wins even
    when the slot cfg has zero labels."""
    cfg = {
        "name": "primary",
        "type": "llm",
        "enabled": True,
        "model": {"default": "agent-7b"},  # no labels key at all
    }
    model_info = {"id": "agent-7b", "capability_flags": {"tool_calling": True}}
    slot = loaded_slot_from_config(cfg, model_info=model_info)
    assert slot is not None
    assert slot.tool_calling is True
    assert slot.labels == frozenset()


def test_loaded_slot_from_config_falls_back_without_model_info() -> None:
    """No model_info at all (registry outage / test stub) -> label fallback."""
    cfg = {
        "name": "primary",
        "type": "llm",
        "enabled": True,
        "model": {"default": "agent-7b", "labels": ["tool-calling"]},
    }
    slot = loaded_slot_from_config(cfg, model_info=None)
    assert slot is not None
    assert slot.tool_calling is True


def test_chat_slot_has_tool_calling_prefers_model_info() -> None:
    cfg = make_slot("p", type="llm", model="x", labels=())
    assert chat_slot_has_tool_calling(cfg) is False
    assert chat_slot_has_tool_calling(cfg, model_info={"capability_flags": {"tool_calling": True}})
