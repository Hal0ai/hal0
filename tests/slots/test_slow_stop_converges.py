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
from unittest.mock import patch

import pytest

from hal0.providers.container import _UNIT_STOP_TIMEOUT_S, terminate_budget_s, unit_stop_bound_s
from hal0.slot_lifecycle_budget import TEARDOWN_MARGIN_S, TERMINATE_TIMEOUT_S
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


# ── a runtime's declared stop grace widens the stop budget ──────────────────
#
# The halogen engine needs up to 120 s to exit cleanly after SIGTERM, and says
# so (``Provider.stop_grace_s``). A 30 s caller-side budget would abandon a
# shutdown that is going exactly as designed, then report the slot as errored
# while the engine is still draining.


@pytest.fixture
def halogen_slow_stub(slow_stub: _SlowStopProvider, slot_root: Path) -> _SlowStopProvider:
    """``slow_stub`` plus a ``flash`` slot whose profile resolves to halogen."""
    (slot_root / "flash.toml").write_text(
        "\n".join(
            [
                'name = "flash"',
                "port = 8085",
                'profile = "halogen"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return slow_stub


async def test_terminate_waits_out_a_halogen_stop_grace(
    halogen_slow_stub: _SlowStopProvider,
) -> None:
    """A stop slower than the generic budget but inside the grace completes."""
    sm = SlotManager()
    sm._terminate_timeout_s = 0.05
    asyncio.get_running_loop().call_later(0.3, halogen_slow_stub.release.set)

    await sm.terminate("flash")  # would raise SlotTerminateTimeout at 0.05 s

    assert [c["name"] for c in halogen_slow_stub.unload_calls] == ["flash"]
    assert "flash" not in halogen_slow_stub.active


async def test_terminate_keeps_the_generic_budget_for_a_slot_without_a_grace(
    halogen_slow_stub: _SlowStopProvider,
) -> None:
    """The contrast: the llama-server ``chat`` slot, same timing, is abandoned."""
    sm = SlotManager()
    sm._terminate_timeout_s = 0.05
    asyncio.get_running_loop().call_later(0.3, halogen_slow_stub.release.set)

    with pytest.raises(SlotTerminateTimeout):
        await sm.terminate("chat")

    await asyncio.sleep(0.4)  # let the abandoned executor stop finish


async def test_unload_waits_out_a_halogen_stop_grace(
    halogen_slow_stub: _SlowStopProvider,
) -> None:
    """``unload`` ends OFFLINE, not in the provisional slow-stop ERROR."""
    sm = SlotManager()
    sm._terminate_timeout_s = 0.05
    await sm._transition("flash", SlotState.READY, force=True)
    asyncio.get_running_loop().call_later(0.3, halogen_slow_stub.release.set)

    slot = await sm.unload("flash")

    assert slot.state is SlotState.OFFLINE
    assert not sm._states[sm._key("flash")].extra.get("slow_stop")


async def test_an_explicit_terminate_budget_is_not_widened(
    halogen_slow_stub: _SlowStopProvider,
) -> None:
    """A caller that passes ``timeout_s`` is giving a deliberate bound."""
    sm = SlotManager()
    asyncio.get_running_loop().call_later(0.3, halogen_slow_stub.release.set)

    with pytest.raises(SlotTerminateTimeout):
        await sm.terminate("flash", timeout_s=0.05)

    await asyncio.sleep(0.4)


def test_terminate_budget_is_the_stop_bound_plus_the_standard_margin(
    halogen_slow_stub: _SlowStopProvider, slot_root: Path
) -> None:
    """A stop that runs its full 150 s still has its Quadlet removal, daemon-reload
    and reset-failed to come, so the wait outlasts it, by the same 10 s the
    30 s / 20 s pair has for a runtime that declares no grace."""
    flash = {"name": "flash", "profile": "halogen"}
    assert unit_stop_bound_s(flash) == 150.0
    assert terminate_budget_s(flash) == 160.0
    assert TEARDOWN_MARGIN_S == TERMINATE_TIMEOUT_S - _UNIT_STOP_TIMEOUT_S == 10.0
    assert terminate_budget_s({"name": "chat"}) is None


async def test_terminate_waits_for_the_budget_not_the_bare_stop_bound(
    halogen_slow_stub: _SlowStopProvider,
) -> None:
    sm = SlotManager()
    sm._terminate_timeout_s = 0.01
    with (
        patch("hal0.providers.container.terminate_budget_s", return_value=0.06),
        pytest.raises(SlotTerminateTimeout, match=r"within 0\.06s"),
    ):
        await sm.terminate("flash")
    halogen_slow_stub.release.set()
