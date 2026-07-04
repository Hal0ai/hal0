"""WARMING is fail-watched — but only on unit liveness, never /health.

A health-wait timeout parks the slot in WARMING (``_await_ready``), which
was previously outside ``_FAIL_WATCH_LIVE_STATES`` — no watcher ran, so a
unit that later died left the slot lying in WARMING forever. WARMING is now
watched with softer semantics: /health failures never strike (a big model
legitimately loads for minutes), only ``_WARMING_INACTIVE_STRIKES``
consecutive is-active failures flip the slot to ERROR.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots.manager import _FAIL_WATCH_LIVE_STATES, SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


@pytest.fixture
def fast_fail_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mgr_mod, "_FAIL_WATCH_INTERVAL_S", 0.1)


def test_warming_is_in_the_watched_set() -> None:
    assert SlotState.WARMING in _FAIL_WATCH_LIVE_STATES


async def _load_into_warming(sm: SlotManager, container_stub: FakeContainerProvider) -> None:
    """Drive load() through a health-wait timeout so the slot parks WARMING."""

    async def _wait_ready_timeout(port: int, timeout_s: float | None = None) -> None:
        raise TimeoutError("health wait timed out")

    container_stub.wait_ready = _wait_ready_timeout  # type: ignore[method-assign]
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.WARMING


async def test_warming_slot_gets_a_watcher_and_survives_failing_health(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """Active unit + failing /health must NOT strike a WARMING slot."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)
    assert "chat" in sm._fail_watchers, "WARMING must spawn a fail-watcher"

    # Model server still loading: /health says not-ok while the unit is up.
    container_stub.healthy = False
    await asyncio.sleep(0.6)  # several poll intervals — plenty of strikes

    assert sm._current_state("chat") == SlotState.WARMING, (
        "a slow-loading model must never be demoted on /health while WARMING"
    )


async def test_warming_slot_flips_error_when_unit_dies(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """A stably-dead unit under a WARMING slot is caught (was the blind spot)."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)

    container_stub.active.discard("chat")

    deadline = asyncio.get_event_loop().time() + 5.0
    observed: Any = None
    while asyncio.get_event_loop().time() < deadline:
        observed = sm._current_state("chat")
        if observed == SlotState.ERROR:
            break
        await asyncio.sleep(0.05)
    assert observed == SlotState.ERROR, (
        f"dead unit under WARMING never surfaced; final state={observed}"
    )
    rec = sm._states["chat"]
    assert "warming" in (rec.message or "").lower()


async def test_warming_slot_tolerates_a_transient_inactive_blip(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_fail_watch: None,
) -> None:
    """One or two inactive reads (unit still activating) must not strike out."""
    sm = SlotManager()
    await _load_into_warming(sm, container_stub)

    # Blip: inactive for ~one poll, then back.
    container_stub.active.discard("chat")
    await asyncio.sleep(0.15)
    container_stub.active.add("chat")
    await asyncio.sleep(0.5)

    assert sm._current_state("chat") == SlotState.WARMING
