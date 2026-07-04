"""The manager's health probes pass the slot config to the provider.

``ContainerProvider.health(port, slot_cfg=None)`` delegates to the FLM
Tier-1 real-inference probe when ``slot_cfg`` resolves to an FLM slot; the
delegation only activates if the manager's two probe call sites actually
supply the config. These tests pin that contract at the manager boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hal0.slots.manager import SlotManager
from tests.slots.conftest import FakeContainerProvider


async def test_probe_health_passes_slot_cfg(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    seen: dict[str, Any] = {}

    async def _health(port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        seen["port"] = port
        seen["cfg"] = slot_cfg
        return {"ok": True}

    container_stub.health = _health  # type: ignore[method-assign]
    sm = SlotManager()
    assert await sm._probe_health("chat") is True
    assert seen["port"] == 8081
    assert isinstance(seen["cfg"], dict) and seen["cfg"]["name"] == "chat"


async def test_container_readiness_check_passes_slot_cfg(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    seen: dict[str, Any] = {}

    async def _health(port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        seen["cfg"] = slot_cfg
        return {"ok": False}

    container_stub.health = _health  # type: ignore[method-assign]
    container_stub.active.add("chat")
    sm = SlotManager()
    ready, reason = await sm.container_readiness_check("chat")
    # ok=False maps to the retryable "starting" (a still-loading FLM model).
    assert (ready, reason) == (False, "starting")
    assert isinstance(seen["cfg"], dict) and seen["cfg"]["name"] == "chat"
