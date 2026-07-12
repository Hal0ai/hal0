"""Restarting an ERROR slot must run the full stop→create→load, not wedge.

Issue #1224: ``POST /api/slots/{name}/restart`` on a slot in ``error`` hung —
``restart()`` funnelled through the graceful ``unload()`` drain against a unit
that had already failed, and the slot never relaunched. ``restart()`` now
force-terminates a wedged unit (clearing systemd's ``failed`` sub-state) and
drops straight to OFFLINE before the reload, so an errored slot recovers to
READY without a manual ``reset-failed``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def _drive_into_error(sm: SlotManager, container_stub: FakeContainerProvider) -> None:
    """Fail the spawn so ``load()`` stamps the slot ERROR and re-raises."""
    container_stub.fail_load = RuntimeError("spawn boom")
    with pytest.raises(RuntimeError):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR


async def test_restart_from_error_reaches_ready(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An errored slot restarted after the fault clears lands READY."""
    sm = SlotManager()
    await _drive_into_error(sm, container_stub)

    # Fault cleared — restart must force the full stop→create→load.
    container_stub.fail_load = None
    await sm.restart("chat")

    assert sm._current_state("chat") == SlotState.READY
    assert container_stub.unload_calls, "restart must terminate the wedged unit"
    assert container_stub.load_calls, "restart must re-spawn the container"


async def test_restart_from_error_does_not_short_circuit(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """ERROR is not a live state — restart must never no-op it as 'loaded'."""
    sm = SlotManager()
    await _drive_into_error(sm, container_stub)

    container_stub.fail_load = None
    container_stub.load_calls.clear()
    container_stub.unload_calls.clear()

    await sm.restart("chat")

    # Both halves of the sequence ran: the unit was torn down and re-spawned.
    assert len(container_stub.unload_calls) >= 1
    assert len(container_stub.load_calls) >= 1
    assert sm._current_state("chat") == SlotState.READY


async def test_restart_from_error_survives_a_hanging_terminate(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminate that blows up must not wedge the restart — it is best-effort
    and the reload still runs (#1224 robustness)."""
    sm = SlotManager()
    await _drive_into_error(sm, container_stub)

    orig_unload_sync = container_stub.unload_sync
    calls = {"n": 0}

    def _boom_once(cfg: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("systemctl stop wedged")
        orig_unload_sync(cfg)

    monkeypatch.setattr(container_stub, "unload_sync", _boom_once)
    container_stub.fail_load = None

    await sm.restart("chat")

    assert sm._current_state("chat") == SlotState.READY
