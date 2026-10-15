"""#2738 — an unload that outlives the stop budget converges to OFFLINE.

``unload()`` used to stamp the budget error as a final ERROR even though the
executor teardown went on to finish (container gone, quadlet removed). The
#2130 convergence needs ``LoadState=loaded`` so it could never retire it.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState, SlotTerminateTimeout
from hal0.upstreams.registry import UpstreamRegistry
from tests.slots.conftest import FakeContainerProvider


class _SlowStopProvider(FakeContainerProvider):
    """``unload_sync`` blocks until ``release`` is set, then tears down."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def unload_sync(self, cfg: dict[str, Any]) -> None:
        assert self.release.wait(timeout=10)
        super().unload_sync(cfg)
        self.removed_cleanly.add(str(cfg.get("name")))


@pytest.fixture
def slow_stub(slot_root: Path, monkeypatch: pytest.MonkeyPatch) -> _SlowStopProvider:
    fake = _SlowStopProvider()
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)
    return fake


def _persisted(sm: SlotManager) -> str:
    return sm._states[sm._key("chat")].state.value


async def test_slow_stop_ends_offline_once_teardown_completes(
    slow_stub: _SlowStopProvider,
) -> None:
    registry = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=registry)
    sm._terminate_timeout_s = 0.05
    await sm.load("chat")
    assert registry.get("chat") is not None

    with pytest.raises(SlotTerminateTimeout):
        await sm.unload("chat")
    # Budget elapsed, teardown still running: provisional ERROR.
    assert sm.state("chat") is SlotState.ERROR

    slow_stub.release.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if sm.state("chat") is SlotState.OFFLINE:
            break

    slot = await sm.status("chat")
    assert slot.state is SlotState.OFFLINE
    assert "converged to offline" in str(slot.metadata.get("message"))
    assert _persisted(sm) == "offline"
    assert registry.get("chat") is None, "upstream must be deregistered late"
    # A brand-new manager (api restart) reads the same OFFLINE from disk.
    assert SlotManager(upstreams_registry=UpstreamRegistry()).state("chat") is SlotState.OFFLINE


async def test_slow_stop_error_converges_in_status_after_restart(
    slow_stub: _SlowStopProvider,
) -> None:
    """No finisher task survives an api restart: ``status()`` must converge."""
    sm = SlotManager()
    await sm.load("chat")
    slow_stub.active.discard("chat")
    await sm._transition(
        "chat",
        SlotState.ERROR,
        message="stopping slot 'chat' did not return within 30.0s",
        extra={"slow_stop": True},
        force=True,
    )

    fresh = SlotManager()
    # Teardown not finished (no probe evidence yet): stays ERROR.
    assert (await fresh.status("chat")).state is SlotState.ERROR
    slow_stub.removed_cleanly.add("chat")
    assert (await fresh.status("chat")).state is SlotState.OFFLINE


async def test_removed_unit_without_slow_stop_mark_keeps_error(
    slow_stub: _SlowStopProvider,
) -> None:
    """The output-sanity teardown shape (unit removed) is a real verdict."""
    sm = SlotManager()
    await sm.load("chat")
    slow_stub.active.discard("chat")
    slow_stub.removed_cleanly.add("chat")
    await sm._transition("chat", SlotState.ERROR, message="garbage output", force=True)

    assert (await sm.status("chat")).state is SlotState.ERROR


async def test_slow_stop_mark_does_not_outlive_its_error(
    slow_stub: _SlowStopProvider,
) -> None:
    """slow-stop ERROR → load → READY → a real ERROR on a removed unit stays ERROR."""
    slow_stub.release.set()
    sm = SlotManager()
    await sm.load("chat")
    await sm._transition(
        "chat", SlotState.ERROR, message="slow", extra={"slow_stop": "tok"}, force=True
    )
    await sm.restart("chat")
    assert sm.state("chat") is SlotState.READY
    assert not sm._states[sm._key("chat")].extra.get("slow_stop")

    slow_stub.active.discard("chat")
    slow_stub.removed_cleanly.add("chat")
    await sm._transition("chat", SlotState.ERROR, message="garbage output", force=True)

    assert (await sm.status("chat")).state is SlotState.ERROR


async def test_watcher_does_not_retire_a_newer_error(
    slow_stub: _SlowStopProvider,
) -> None:
    sm = SlotManager()
    sm._terminate_timeout_s = 0.05
    await sm.load("chat")
    with pytest.raises(SlotTerminateTimeout):
        await sm.unload("chat")
    # Operator intervenes: a different, genuine ERROR lands before the
    # abandoned stop resolves.
    await sm._transition("chat", SlotState.ERROR, message="real failure", force=True)

    slow_stub.release.set()
    await asyncio.gather(*sm._slow_stop_tasks)

    assert sm.state("chat") is SlotState.ERROR
    assert "real failure" in sm._states[sm._key("chat")].message
