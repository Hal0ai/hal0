"""FLMTrioRouter: static-port resolution for containerized npu slot (Phase A).

Covers:
  - ready container npu slot → static port, lemond never called
  - serving container npu slot → static port (concurrent request mid-inference)
  - non-ready container npu slot → falls back to lemond walk
  - no slot_manager → legacy lemond path unchanged
  - disabled container npu slot → fallback
  - lemonade-runtime npu slot (no profile, no runtime=container) → fallback
  - slot_manager accessor raising → fallback (never crash dispatch)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.dispatcher.flm_trio import FLMTrioRouter
from hal0.slots.state import SlotState

# ── Helpers ────────────────────────────────────────────────────────────


def _make_slot(state: str) -> MagicMock:
    """Build a Slot-like mock whose .state is a SlotState enum value."""
    slot = MagicMock()
    slot.state = SlotState(state)
    return slot


def _slot_manager_with_container_npu(
    state: str = "ready",
    *,
    enabled: bool = True,
    profile: str = "flm-npu",
    runtime: str | None = None,
    port: int = 8088,
) -> MagicMock:
    """SlotManager mock for a container npu slot."""
    sm = MagicMock()
    cfg: dict[str, Any] = {
        "name": "npu",
        "port": port,
        "device": "npu",
        "enabled": enabled,
    }
    if profile:
        cfg["profile"] = profile
    if runtime is not None:
        cfg["runtime"] = runtime
    sm.get_config = AsyncMock(return_value=cfg)
    sm.status = AsyncMock(return_value=_make_slot(state))
    return sm


def _slot_manager_with_lemonade_npu() -> MagicMock:
    """SlotManager mock for a lemond-runtime npu slot (no profile, no container runtime)."""
    sm = MagicMock()
    sm.get_config = AsyncMock(
        return_value={
            "name": "npu",
            "port": 8099,
            "device": "npu",
            "enabled": True,
            # no profile, no runtime=container
        }
    )
    sm.status = AsyncMock(return_value=_make_slot("ready"))
    return sm


def _lemonade_with_flm_loaded(url: str = "http://127.0.0.1:8201/v1") -> MagicMock:
    lemonade = MagicMock()
    lemonade.health = AsyncMock(
        return_value={"loaded": [{"recipe": "flm", "type": "llm", "backend_url": url}]}
    )
    return lemonade


# ── Static-port resolution ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_container_npu_resolves_static_port() -> None:
    """Ready container npu slot → static URL, lemond.health never called."""
    lemonade = MagicMock()
    lemonade.health = AsyncMock(side_effect=AssertionError("must not hit lemond"))
    router = FLMTrioRouter(lemonade, slot_manager=_slot_manager_with_container_npu())
    assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8088"


@pytest.mark.asyncio
async def test_container_npu_via_runtime_field_resolves_static_port() -> None:
    """runtime='container' with no profile also qualifies as container slot."""
    lemonade = MagicMock()
    lemonade.health = AsyncMock(side_effect=AssertionError("must not hit lemond"))
    sm = _slot_manager_with_container_npu(profile="", runtime="container", port=9090)
    router = FLMTrioRouter(lemonade, slot_manager=sm)
    assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_serving_container_npu_resolves_static_port() -> None:
    """SERVING (inference in flight) still resolves — a concurrent STT/embed
    request mid-inference must NOT fall back to the lemond walk."""
    lemonade = MagicMock()
    lemonade.health = AsyncMock(side_effect=AssertionError("must not hit lemond"))
    router = FLMTrioRouter(
        lemonade,
        slot_manager=_slot_manager_with_container_npu(state="serving"),
    )
    assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8088"


# ── Fallback: non-ready state ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_ready_container_falls_back_to_lemond() -> None:
    """Container npu slot still starting up → lemond walk.

    Uses SlotState.STARTING — the closest real lifecycle state to the
    "container launched but not yet ready" window.
    """
    lemonade = _lemonade_with_flm_loaded()
    router = FLMTrioRouter(
        lemonade,
        slot_manager=_slot_manager_with_container_npu(state="starting"),
    )
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"


@pytest.mark.asyncio
async def test_offline_container_falls_back_to_lemond() -> None:
    """offline → fallback."""
    lemonade = _lemonade_with_flm_loaded()
    router = FLMTrioRouter(
        lemonade,
        slot_manager=_slot_manager_with_container_npu(state="offline"),
    )
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"


# ── Fallback: no slot_manager ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_slot_manager_keeps_legacy_path() -> None:
    """No slot_manager wired → pure lemond walk, byte-identical to prior behaviour."""
    lemonade = _lemonade_with_flm_loaded()
    router = FLMTrioRouter(lemonade)
    assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8201"


# ── Fallback: disabled slot ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_container_falls_back_to_lemond() -> None:
    """enabled=False → not a live container target → lemond walk."""
    lemonade = _lemonade_with_flm_loaded()
    router = FLMTrioRouter(
        lemonade,
        slot_manager=_slot_manager_with_container_npu(enabled=False),
    )
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"


# ── Fallback: non-container npu (lemond-managed) ───────────────────────


@pytest.mark.asyncio
async def test_lemonade_runtime_npu_falls_back_to_lemond() -> None:
    """npu slot without profile + without runtime=container → lemond walk."""
    lemonade = _lemonade_with_flm_loaded()
    router = FLMTrioRouter(lemonade, slot_manager=_slot_manager_with_lemonade_npu())
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"


# ── Fallback: accessor errors ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_raises_falls_back_to_lemond() -> None:
    """get_config raising → swallowed, fall through to lemond walk."""
    lemonade = _lemonade_with_flm_loaded()
    sm = MagicMock()
    sm.get_config = AsyncMock(side_effect=RuntimeError("TOML missing"))
    router = FLMTrioRouter(lemonade, slot_manager=sm)
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"


@pytest.mark.asyncio
async def test_status_raises_falls_back_to_lemond() -> None:
    """status() raising → swallowed, fall through to lemond walk."""
    lemonade = _lemonade_with_flm_loaded()
    sm = MagicMock()
    sm.get_config = AsyncMock(
        return_value={
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "profile": "flm-npu",
            "enabled": True,
        }
    )
    sm.status = AsyncMock(side_effect=RuntimeError("state file corrupt"))
    router = FLMTrioRouter(lemonade, slot_manager=sm)
    result = await router.find_flm_chat_backend_url()
    assert result == "http://127.0.0.1:8201"
