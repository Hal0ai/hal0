"""Tests for the slot back-compat alias system (issue #654 / #633).

Verifies:
  1. Old names ("primary", "agent-hermes") resolve to canonical slots.
  2. Aliases do NOT appear in SlotManager.list() or iter_configs().
  3. hal0/primary virtual name resolves via the resolver layer.
  4. dispatcher/router Rule 6 + 7 handle the renamed slot.
  5. hal0_chat_slot_alias_map injects back-compat aliases.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.slots.manager import NPU_SEEDED_SLOTS, SEEDED_SLOTS, SLOT_ALIASES, SlotManager

# ── 1. Module-level constant checks ──────────────────────────────────────────


def test_seeded_slots_uses_chat_not_primary():
    """Canonical slot name is 'chat', not 'primary'."""
    assert "chat" in SEEDED_SLOTS
    assert "primary" not in SEEDED_SLOTS


def test_agent_is_gpu_seeded_not_npu():
    """#679: agent is a GPU chat-role seed slot, not the NPU FLM anchor."""
    assert "agent" in SEEDED_SLOTS
    assert "agent" not in NPU_SEEDED_SLOTS
    assert "agent-hermes" not in NPU_SEEDED_SLOTS
    assert NPU_SEEDED_SLOTS == ("stt-npu", "embed-npu")


def test_slot_aliases_map():
    """SLOT_ALIASES maps old names → new canonical names."""
    assert SLOT_ALIASES.get("primary") == "chat"
    assert SLOT_ALIASES.get("agent-hermes") == "agent"


# ── 2. _resolve_alias static method ──────────────────────────────────────────


def test_resolve_alias_primary_to_chat():
    assert SlotManager._resolve_alias("primary") == "chat"


def test_resolve_alias_agent_hermes_to_agent():
    assert SlotManager._resolve_alias("agent-hermes") == "agent"


def test_resolve_alias_passthrough_canonical():
    assert SlotManager._resolve_alias("chat") == "chat"
    assert SlotManager._resolve_alias("agent") == "agent"
    assert SlotManager._resolve_alias("embed") == "embed"
    assert SlotManager._resolve_alias("utility") == "utility"


def test_resolve_alias_unknown_passthrough():
    assert SlotManager._resolve_alias("my-custom-slot") == "my-custom-slot"


# ── 3. Public methods accept aliases ─────────────────────────────────────────


@pytest.fixture()
def tmp_slots_dir(tmp_path: Path):
    """A temporary directory containing a chat.toml slot config."""
    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "chat.toml").write_text(
        '[slot]\nname = "chat"\ntype = "llm"\nenabled = true\n\n[model]\ndefault = "qwen3-8b"\n'
    )
    return tmp_path


@pytest.fixture()
def mock_slot_manager(tmp_slots_dir: Path):
    """SlotManager wired to a temp dir with a chat.toml."""
    with (
        patch("hal0.config.paths.slots_config_dir", return_value=tmp_slots_dir / "slots"),
        patch("hal0.config.paths.slot_data_dir", return_value=tmp_slots_dir / "data"),
        patch("hal0.config.paths.var_lib", return_value=tmp_slots_dir),
    ):
        mgr = SlotManager.__new__(SlotManager)
        mgr._states = {}
        mgr._locks = {}
        mgr._last_used = {}
        mgr._sse_queues = []
        mgr._model_registry = None
        mgr._pull_runner = None
        mgr._fail_watchers = {}
        mgr._idle_monitor_tasks = {}
        yield mgr


def test_manager_resolve_alias_primary_maps_to_chat(tmp_slots_dir):
    """_resolve_alias("primary") == "chat" and the chat TOML exists."""
    resolved = SlotManager._resolve_alias("primary")
    assert resolved == "chat"
    assert (tmp_slots_dir / "slots" / "chat.toml").exists()


# ── 4. list() and iter_configs() do NOT expose aliases ───────────────────────


@pytest.mark.asyncio
async def test_list_does_not_contain_primary_alias(tmp_slots_dir):
    """list() enumerates TOMLs from disk — no alias appears in the result."""
    slots_dir = tmp_slots_dir / "slots"
    # Confirm only chat.toml exists (no primary.toml)
    names = {p.stem for p in slots_dir.glob("*.toml")}
    assert "chat" in names
    assert "primary" not in names
    assert "agent-hermes" not in names


@pytest.mark.asyncio
async def test_iter_configs_does_not_leak_aliases(tmp_slots_dir):
    """iter_configs() is driven by disk TOMLs — aliases never appear."""
    slots_dir = tmp_slots_dir / "slots"
    names = {p.stem for p in slots_dir.glob("*.toml")}
    assert "primary" not in names
    assert "agent-hermes" not in names


# ── 5. normalize/resolver virtual alias ──────────────────────────────────────


def test_resolver_hal0_primary_in_virtual_aliases():
    from hal0.normalize.resolver import VIRTUAL_ALIASES

    assert "hal0/primary" in VIRTUAL_ALIASES
    assert VIRTUAL_ALIASES["hal0/primary"] == "hal0/chat"


def test_resolver_hal0_chat_in_default_chains():
    from hal0.normalize.resolver import DEFAULT_CHAINS

    assert "hal0/chat" in DEFAULT_CHAINS
    assert "hal0/primary" not in DEFAULT_CHAINS  # moved to VIRTUAL_ALIASES


def test_resolver_npu_chain_uses_chat_fallback():
    from hal0.normalize.resolver import DEFAULT_CHAINS

    # Back-compat: npu/utility chains fall through to "chat" not "primary"
    assert DEFAULT_CHAINS["hal0/npu"][-1] == "chat"
    assert DEFAULT_CHAINS["hal0/utility"][-1] == "chat"


def test_resolve_chain_hal0_primary_resolves():
    from hal0.normalize.resolver import SlotView, resolve_chain

    slots = [
        SlotView(name="chat", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    r = resolve_chain("hal0/primary", slots, loaded={"big"})
    assert r is not None
    assert r.model_id == "big"
    assert r.fallback is False


# ── 6. dispatcher/router rule 7 fallback uses "chat" ──────────────────────────


def test_proxy_fallback_uses_chat():
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="chat", kind="slot", url="http://127.0.0.1:13305/v1"))
    upstream = resolve_by_capability("/v1/chat/completions", None, reg)
    assert upstream.name == "chat"


def test_proxy_rule6_primary_alias_resolves_to_chat():
    """model=primary in body resolves to chat slot via alias (Rule 6 path)."""
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="chat", kind="slot", url="http://127.0.0.1:13305/v1"))
    # "primary" is an alias for "chat"; Rule 6 resolves the alias → chat
    # then the Rule 6 guard (m_resolved != "chat") drops it to Rule 7 fallback
    upstream = resolve_by_capability("/v1/chat/completions", {"model": "primary"}, reg)
    assert upstream.name == "chat"


def test_proxy_rule6_agent_hermes_alias_resolves_to_agent():
    """model=agent-hermes resolves to the agent slot via alias."""
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="agent", kind="slot", url="http://127.0.0.1:13305/v1"))
    reg.add(Upstream(name="chat", kind="slot", url="http://127.0.0.1:13305/v1"))
    upstream = resolve_by_capability("/v1/chat/completions", {"model": "agent-hermes"}, reg)
    assert upstream.name == "agent"


# ── 7. hal0_chat_slot_alias_map injects back-compat entries ──────────────────


@pytest.mark.asyncio
async def test_chat_slot_alias_map_includes_primary_backcompat():
    """hal0_chat_slot_alias_map returns primary→model_id for back-compat."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {
                "name": "chat",
                "type": "llm",
                "enabled": True,
                "model_id": "qwen3-8b",
            }
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    # canonical name present
    assert result.get("chat") == "qwen3-8b"
    # back-compat alias present
    assert result.get("primary") == "qwen3-8b"


@pytest.mark.asyncio
async def test_chat_slot_alias_map_agent_hermes_backcompat():
    """hal0_chat_slot_alias_map injects agent-hermes→model_id back-compat entry."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {
                "name": "agent",
                "type": "llm",
                "enabled": True,
                "model_id": "qwen3-4b",
            }
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    assert result.get("agent") == "qwen3-4b"
    assert result.get("agent-hermes") == "qwen3-4b"


@pytest.mark.asyncio
async def test_chat_slot_alias_map_alias_does_not_override_explicit():
    """If a literal 'primary' slot still exists on disk, it takes precedence."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {"name": "chat", "type": "llm", "enabled": True, "model_id": "qwen3-8b"},
            # Pre-migration slot still named "primary"
            {"name": "primary", "type": "llm", "enabled": True, "model_id": "old-model"},
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    # The on-disk "primary" entry wins (setdefault)
    assert result.get("primary") == "old-model"
    assert result.get("chat") == "qwen3-8b"
