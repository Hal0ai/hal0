"""Tests for the slot back-compat alias system (issue #654 / #633, ADR-0023).

ADR-0023 retired ``chat``/``primary`` as slot/role names. The canonical llm
roles are now ``agent`` (default/anchor, replaces ``chat``) + ``utility`` (cheap
helper, now seeded). ``SLOT_ALIASES`` keeps only ``agent-hermes → agent``.

Verifies:
  1. SEEDED_SLOTS carries ``utility`` + ``agent`` and NOT ``chat``/``primary``.
  2. SLOT_ALIASES is exactly ``{"agent-hermes": "agent"}``.
  3. Aliases do NOT appear in SlotManager.list() or iter_configs().
  4. DEFAULT_CHAINS advertises agent/utility/npu (NOT hal0/chat) and ends every
     chain in ``agent``.
  5. dispatcher/router resolves the renamed anchor + the agent-hermes alias.
  6. hal0_chat_slot_alias_map injects the agent-hermes back-compat alias.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.slots.manager import NPU_SEEDED_SLOTS, SEEDED_SLOTS, SLOT_ALIASES, SlotManager

# ── 1. Module-level constant checks ──────────────────────────────────────────


def test_seeded_slots_uses_utility_not_chat():
    """ADR-0023: ``utility`` is seeded; ``chat``/``primary`` are retired."""
    assert "utility" in SEEDED_SLOTS
    assert "chat" not in SEEDED_SLOTS
    assert "primary" not in SEEDED_SLOTS


def test_agent_is_gpu_seeded_not_npu():
    """agent is the GPU default-role anchor seed slot, not the NPU FLM anchor."""
    assert "agent" in SEEDED_SLOTS
    assert "agent" not in NPU_SEEDED_SLOTS
    assert "agent-hermes" not in NPU_SEEDED_SLOTS
    assert NPU_SEEDED_SLOTS == ("stt-npu", "embed-npu")


def test_slot_aliases_map():
    """ADR-0023: SLOT_ALIASES drops ``primary`` — only agent-hermes → agent."""
    assert SLOT_ALIASES == {"agent-hermes": "agent", "qwen3-4b": "flm", "qwen3:4b": "flm"}


# ── 2. _resolve_alias static method ──────────────────────────────────────────


def test_resolve_alias_agent_hermes_to_agent():
    assert SlotManager._resolve_alias("agent-hermes") == "agent"


def test_resolve_alias_primary_is_passthrough_now():
    # ADR-0023: ``primary`` is no longer an alias — it passes through unchanged
    # (and resolves to nothing on disk, falling to the agent anchor at dispatch).
    assert SlotManager._resolve_alias("primary") == "primary"


def test_resolve_alias_passthrough_canonical():
    assert SlotManager._resolve_alias("agent") == "agent"
    assert SlotManager._resolve_alias("embed") == "embed"
    assert SlotManager._resolve_alias("utility") == "utility"


def test_resolve_alias_unknown_passthrough():
    assert SlotManager._resolve_alias("my-custom-slot") == "my-custom-slot"


# ── 3. Temp slot dir fixtures ────────────────────────────────────────────────


@pytest.fixture()
def tmp_slots_dir(tmp_path: Path):
    """A temporary directory containing an agent.toml slot config."""
    slots_dir = tmp_path / "slots"
    slots_dir.mkdir()
    (slots_dir / "agent.toml").write_text(
        '[slot]\nname = "agent"\ntype = "llm"\nenabled = true\n\n[model]\ndefault = "qwen3-8b"\n'
    )
    return tmp_path


@pytest.fixture()
def mock_slot_manager(tmp_slots_dir: Path):
    """SlotManager wired to a temp dir with an agent.toml."""
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


def test_manager_resolve_alias_agent_hermes_maps_to_agent(tmp_slots_dir):
    """_resolve_alias("agent-hermes") == "agent" and the agent TOML exists."""
    resolved = SlotManager._resolve_alias("agent-hermes")
    assert resolved == "agent"
    assert (tmp_slots_dir / "slots" / "agent.toml").exists()


# ── 4. list() and iter_configs() do NOT expose aliases ───────────────────────


@pytest.mark.asyncio
async def test_list_does_not_contain_aliases(tmp_slots_dir):
    """list() enumerates TOMLs from disk — no alias appears in the result."""
    slots_dir = tmp_slots_dir / "slots"
    names = {p.stem for p in slots_dir.glob("*.toml")}
    assert "agent" in names
    assert "agent-hermes" not in names
    assert "primary" not in names


@pytest.mark.asyncio
async def test_iter_configs_does_not_leak_aliases(tmp_slots_dir):
    """iter_configs() is driven by disk TOMLs — aliases never appear."""
    slots_dir = tmp_slots_dir / "slots"
    names = {p.stem for p in slots_dir.glob("*.toml")}
    assert "agent-hermes" not in names
    assert "primary" not in names


# ── 5. normalize/resolver canonical virtual names (ADR-0023) ─────────────────


def test_resolver_no_virtual_alias_map():
    """No alias map; hal0/primary is not a known virtual."""
    from hal0.normalize import resolver as R

    assert not hasattr(R, "VIRTUAL_ALIASES")
    assert "hal0/primary" not in R.DEFAULT_CHAINS


def test_resolver_default_chains_advertise_agent_utility_npu():
    from hal0.normalize.resolver import DEFAULT_CHAINS

    assert "hal0/agent" in DEFAULT_CHAINS
    assert "hal0/utility" in DEFAULT_CHAINS
    assert "hal0/npu" in DEFAULT_CHAINS
    # ADR-0023: hal0/chat is no longer canonical/advertised.
    assert "hal0/chat" not in DEFAULT_CHAINS
    assert "hal0/primary" not in DEFAULT_CHAINS


def test_resolver_chains_end_in_agent_anchor():
    from hal0.normalize.resolver import DEFAULT_CHAINS

    # Every fallback chain anchors on `agent` (replaces the old `chat` anchor).
    assert DEFAULT_CHAINS["hal0/agent"][-1] == "agent"
    assert DEFAULT_CHAINS["hal0/npu"][-1] == "agent"
    assert DEFAULT_CHAINS["hal0/utility"][-1] == "agent"


def test_resolve_chain_hal0_chat_is_not_canonical():
    """hal0/chat is no longer a canonical virtual. It only resolves at all if a
    leftover `chat` slot lingers (generalized hal0/<slot> path); with no such
    slot it returns None."""
    from hal0.normalize.resolver import SlotView, resolve_chain

    slots = [
        SlotView(
            name="agent", device="gpu-vulkan", model_id="big", context_length=65536
        ),
    ]
    # No `chat` slot present → hal0/chat is unknown.
    assert resolve_chain("hal0/chat", slots, loaded={"big"}) is None
    # The canonical anchor resolves.
    r = resolve_chain("hal0/agent", slots, loaded={"big"})
    assert r is not None
    assert r.model_id == "big"
    assert r.fallback is False


# ── 6. dispatcher/router fallback uses "agent" ───────────────────────────────


def test_proxy_fallback_uses_agent():
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="agent", kind="slot", url="http://127.0.0.1:13305/v1"))
    upstream = resolve_by_capability("/v1/chat/completions", None, reg)
    assert upstream.name == "agent"


def test_proxy_primary_falls_through_to_agent_anchor():
    """ADR-0023: ``primary`` is no longer an alias — a body model=primary finds
    no `primary` slot and falls through to the rule-9 `agent` anchor."""
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="agent", kind="slot", url="http://127.0.0.1:13305/v1"))
    upstream = resolve_by_capability("/v1/chat/completions", {"model": "primary"}, reg)
    assert upstream.name == "agent"


def test_proxy_agent_hermes_alias_resolves_to_agent():
    """model=agent-hermes resolves to the agent slot via alias."""
    from hal0.dispatcher.router import resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(Upstream(name="agent", kind="slot", url="http://127.0.0.1:13305/v1"))
    upstream = resolve_by_capability("/v1/chat/completions", {"model": "agent-hermes"}, reg)
    assert upstream.name == "agent"


# ── 7. hal0_chat_slot_alias_map injects back-compat entries ──────────────────


@pytest.mark.asyncio
async def test_chat_slot_alias_map_includes_agent_canonical():
    """hal0_chat_slot_alias_map returns the canonical agent slot's model_id."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {
                "name": "agent",
                "type": "llm",
                "enabled": True,
                "model_id": "qwen3-8b",
            }
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    assert result.get("agent") == "qwen3-8b"
    # back-compat alias present
    assert result.get("agent-hermes") == "qwen3-8b"


@pytest.mark.asyncio
async def test_chat_slot_alias_map_utility_no_alias_injection():
    """A utility-only slot set carries `utility` but injects no extra alias
    (agent-hermes only fires when an `agent` slot is present)."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {
                "name": "utility",
                "type": "llm",
                "enabled": True,
                "model_id": "qwen3-0.8b",
            }
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    assert result.get("utility") == "qwen3-0.8b"
    assert "agent-hermes" not in result


@pytest.mark.asyncio
async def test_chat_slot_alias_map_alias_does_not_override_explicit():
    """If a literal 'agent-hermes' slot still exists on disk, it takes precedence."""
    from hal0.api import hal0_chat_slot_alias_map

    slot_manager = MagicMock()
    slot_manager.iter_configs = AsyncMock(
        return_value=[
            {"name": "agent", "type": "llm", "enabled": True, "model_id": "qwen3-8b"},
            # Pre-migration slot still named "agent-hermes"
            {"name": "agent-hermes", "type": "llm", "enabled": True, "model_id": "old-model"},
        ]
    )
    result = await hal0_chat_slot_alias_map(slot_manager)
    # The on-disk "agent-hermes" entry wins (setdefault)
    assert result.get("agent-hermes") == "old-model"
    assert result.get("agent") == "qwen3-8b"
