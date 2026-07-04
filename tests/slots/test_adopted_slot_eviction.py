"""Adopted / restart-surviving slots participate in idle + pressure eviction.

Two halves of the fix:

  1. ``_maybe_adopt_running_slot`` bumps ``last_used`` so an adopted slot's
     idle clock starts at adoption instead of never.
  2. ``_sweep_candidates`` falls back to the state record's ``updated_at``
     for dispatchable slots missing from ``_last_used`` entirely, so both
     ``_sweep_idle_once`` and ``_pressure_evict_once`` can see them.

Pre-fix, a container that outlived an api restart was invisible to both
sweeps (they iterated only ``_last_used``) and squatted on RAM forever.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def test_adoption_bumps_last_used(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Adopting a running slot starts its idle clock."""
    sm = SlotManager()
    container_stub.active.add("chat")
    assert "chat" not in sm._last_used

    snap = await sm.status("chat")  # no state.json + active unit → adoption
    assert snap.state == SlotState.READY
    assert snap.metadata.get("adopted") is True
    assert "chat" in sm._last_used


async def test_adoption_records_effective_backend_not_hardcoded_vulkan(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The adopted extras carry the device/backend-derived token."""
    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'device = "gpu-rocm"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager()
    container_stub.active.add("chat")
    snap = await sm.status("chat")
    assert snap.metadata.get("adopted") is True
    assert snap.backend == "rocm"


async def test_sweep_candidates_falls_back_to_state_updated_at(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A READY slot absent from _last_used surfaces via state.json updated_at."""
    sm = SlotManager()
    container_stub.active.add("chat")
    await sm._transition("chat", SlotState.READY, force=True)
    sm._last_used.pop("chat", None)

    candidates = sm._sweep_candidates()
    assert "chat" in candidates
    assert candidates["chat"] == sm._states["chat"].updated_at


async def test_idle_sweep_evicts_slot_missing_from_last_used(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The TTL sweep unloads a dispatchable slot it previously couldn't see."""
    sm = SlotManager(evict_after_s=0.05)
    container_stub.active.add("chat")
    await sm._transition("chat", SlotState.READY, force=True)
    sm._last_used.pop("chat", None)

    await asyncio.sleep(0.1)  # exceed the tiny TTL
    await sm._sweep_idle_once()

    assert sm._current_state("chat") == SlotState.OFFLINE
    assert container_stub.unload_calls, "eviction must dispatch a real unload"


async def test_pressure_sweep_sees_slot_missing_from_last_used(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch,
) -> None:
    """Pressure eviction reclaims an lru slot known only via state.json."""
    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'provider = "llama-server"',
                "enabled = true",
                "lru = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager(evict_pressure_mb=1024)
    container_stub.active.add("chat")
    await sm._transition("chat", SlotState.READY, force=True)
    sm._last_used.pop("chat", None)
    # Host reads as under pressure on every probe.
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 128.0)

    await sm._pressure_evict_once()

    assert sm._current_state("chat") == SlotState.OFFLINE
