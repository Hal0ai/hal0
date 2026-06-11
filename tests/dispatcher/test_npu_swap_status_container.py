"""npu_swap_status: containerized npu reports swap from slot state (Phase A).

Tests the container branch of fetch_npu_swap_status — slot lifecycle
state drives the in_progress signal when the enabled NPU LLM slot is a
container slot (profile set or runtime=="container").
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.dispatcher.npu_swap_status import fetch_npu_swap_status
from hal0.slots.state import SlotState

# ── Helpers ────────────────────────────────────────────────────────────


def _make_slot(state: str) -> MagicMock:
    """Build a Slot-like mock whose .state is a real SlotState enum value."""
    slot = MagicMock()
    slot.state = SlotState(state)
    return slot


def _container_npu_cfg(
    model: str = "gemma3:4b",
    *,
    profile: str = "flm-npu",
    runtime: str | None = None,
    enabled: bool = True,
    name: str = "npu",
) -> dict[str, Any]:
    """Slot config for a containerized NPU LLM slot."""
    cfg: dict[str, Any] = {
        "name": name,
        "device": "npu",
        "type": "llm",
        "enabled": enabled,
        "model": {"default": model},
    }
    if profile:
        cfg["profile"] = profile
    if runtime is not None:
        cfg["runtime"] = runtime
    return cfg


def _lemonade_npu_cfg(model: str = "llama-3.2-3b-npu") -> dict[str, Any]:
    """Slot config for a Lemonade-managed (non-container) NPU LLM slot."""
    return {
        "name": "agent",
        "device": "npu",
        "type": "llm",
        "enabled": True,
        "model": {"default": model},
        # no profile, no runtime=container
    }


def _slot_manager(state: str = "ready", cfg: dict[str, Any] | None = None) -> MagicMock:
    """SlotManager mock returning the given config and slot state."""
    sm = MagicMock()
    sm.get_config = AsyncMock(return_value=cfg or _container_npu_cfg())
    sm.status = AsyncMock(return_value=_make_slot(state))
    return sm


def _no_client() -> None:
    """Sentinel for lemonade_client=None."""
    return None


# ── Container path: transitional states → in_progress=True ────────────


@pytest.mark.anyio
async def test_container_starting_means_swap_in_progress() -> None:
    """SlotState.STARTING → in_progress=True (container restarting for model swap)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="starting", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"
    # container path has no "from" side (no previously-loaded signal)
    assert status.from_model is None


@pytest.mark.anyio
async def test_container_pulling_means_swap_in_progress() -> None:
    """SlotState.PULLING → in_progress=True."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="pulling", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"


@pytest.mark.anyio
async def test_container_warming_means_swap_in_progress() -> None:
    """SlotState.WARMING → in_progress=True."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="warming", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is True


@pytest.mark.anyio
async def test_container_unloading_means_swap_in_progress() -> None:
    """SlotState.UNLOADING → in_progress=True (transition still in flight)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="unloading", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is True


# ── Container path: settled states → in_progress=False ────────────────


@pytest.mark.anyio
async def test_container_ready_means_settled() -> None:
    """SlotState.READY → in_progress=False, to_model populated from config."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="ready", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is False
    assert status.to_model == "gemma3:4b"
    assert status.from_model is None


@pytest.mark.anyio
async def test_container_serving_means_settled() -> None:
    """SlotState.SERVING → in_progress=False (inference in flight, not a swap)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="serving", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is False


@pytest.mark.anyio
async def test_container_offline_means_settled() -> None:
    """SlotState.OFFLINE → in_progress=False (slot is not running)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="offline", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is False


@pytest.mark.anyio
async def test_container_error_means_settled() -> None:
    """SlotState.ERROR → in_progress=False (swap not in progress)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = _slot_manager(state="error", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is False


# ── Container path: runtime="container" alternative detection ──────────


@pytest.mark.anyio
async def test_container_via_runtime_field_also_uses_container_path() -> None:
    """runtime='container' with no profile also triggers the container path."""
    cfg = _container_npu_cfg(model="gemma3:4b", profile="", runtime="container")
    sm = _slot_manager(state="starting", cfg=cfg)

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is True
    assert status.to_model == "gemma3:4b"


# ── Legacy lemond path: non-container NPU slot ─────────────────────────


@pytest.mark.anyio
async def test_lemonade_npu_slot_keeps_legacy_path() -> None:
    """Non-container NPU slot (no profile, no runtime=container) → lemond diff path.

    The slot_manager is wired but the NPU slot is NOT a container slot,
    so is_container_npu_cfg returns False and the lemond client must be
    consulted for the health probe.
    """
    cfg = _lemonade_npu_cfg(model="llama-3.2-3b-npu")

    # SlotManager mock that would return a lemonade-runtime config
    sm = MagicMock()
    sm.get_config = AsyncMock(return_value=cfg)
    sm.status = AsyncMock(return_value=_make_slot("ready"))

    lemonade = MagicMock()
    lemonade.health = AsyncMock(
        return_value={
            "loaded": [
                {
                    "model_name": "gemma3:1b",
                    "backend_url": "http://127.0.0.1:9001",
                    "recipe": "flm",
                    "type": "llm",
                }
            ]
        }
    )

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=lemonade,
        slot_manager=sm,
    )

    # lemond consulted: loaded model differs from configured → swap
    lemonade.health.assert_awaited_once()
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"
    assert status.to_model == "llama-3.2-3b-npu"


@pytest.mark.anyio
async def test_no_slot_manager_keeps_legacy_path() -> None:
    """No slot_manager wired → pure lemond walk, same as pre-A6 behaviour."""
    cfg = _lemonade_npu_cfg(model="llama-3.2-3b-npu")

    lemonade = MagicMock()
    lemonade.health = AsyncMock(
        return_value={
            "loaded": [
                {
                    "model_name": "gemma3:1b",
                    "backend_url": "http://127.0.0.1:9001",
                    "recipe": "flm",
                    "type": "llm",
                }
            ]
        }
    )

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=lemonade,
        # slot_manager not passed → defaults to None
    )

    lemonade.health.assert_awaited_once()
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"


# ── Container path: accessor error resilience ──────────────────────────


@pytest.mark.anyio
async def test_container_status_raises_degrades_to_settled() -> None:
    """slot_manager.status() raising → swallowed, in_progress=False (safe degrade)."""
    cfg = _container_npu_cfg(model="gemma3:4b")
    sm = MagicMock()
    sm.status = AsyncMock(side_effect=RuntimeError("state file corrupt"))

    status = await fetch_npu_swap_status(
        slot_configs=[cfg],
        lemonade_client=None,
        slot_manager=sm,
    )

    assert status.in_progress is False
    assert status.to_model == "gemma3:4b"


__all__: list[str] = []
