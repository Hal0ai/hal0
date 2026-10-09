"""#2442: a slot ADOPTED into WARMING must be promoted once its server is up.

``_maybe_adopt_running_slot`` probes ``/health`` once and parks a still-loading
container in WARMING. The fail-watcher deliberately never probes ``/health``
during WARMING (a slow cold load must not be killed), so before this fix
nothing ever moved an adopted slot to READY: no upstream, no model-cache
entry, and ``hal0/<name>`` silently fell back to the anchor slot.

Adoption now arms a bounded ``/health`` poll that promotes the slot through
the same tail a normal load uses (register the upstream, transition to READY,
which fires the ``slot.state`` ready event the api's model-cache refresher
keys on). Normal cold loads get no such poll.

The boot half: a WARMING state persisted in state.json is read at startup
without a ``_transition``, so no watcher was ever armed. Startup reconcile
now adopts it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState, SlotStateRecord, write_state_atomic
from hal0.upstreams.registry import UpstreamRegistry

from .conftest import FakeContainerProvider


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, type_: str, severity: str, source: str, msg: str, **kw: Any) -> None:
        self.events.append({"type": type_, "data": kw.get("data") or {}})


@pytest.fixture
def fast_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mgr_mod, "_ADOPTED_WARMING_POLL_INTERVAL_S", 0.05)


async def _wait_for(pred: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


async def _adopt_into_warming(sm: SlotManager, stub: FakeContainerProvider) -> None:
    stub.active.add("chat")
    stub.healthy = False  # model server still loading
    snap = await sm.status("chat")
    assert snap.state is SlotState.WARMING


async def test_adopted_warming_promotes_and_registers_upstream(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    reg = UpstreamRegistry()
    bus = _RecordingBus()
    sm = SlotManager(upstreams_registry=reg, event_bus=bus)
    await _adopt_into_warming(sm, container_stub)
    assert sm._key("chat") in sm._adopt_promoters
    assert reg.get("chat") is None  # status() adoption does not route WARMING

    # Still loading: several polls must leave it WARMING.
    await asyncio.sleep(0.3)
    assert sm._current_state("chat") is SlotState.WARMING

    container_stub.healthy = True
    assert await _wait_for(lambda: sm._current_state("chat") is SlotState.READY)

    up = reg.get("chat")
    assert up is not None
    assert up.kind == "slot"
    assert ":8081" in up.url
    # The ready event is what the api's model-cache refresher keys on.
    assert any(
        e["type"] == "slot.state" and e["data"].get("to") == "ready" and e["data"]["slot"] == "chat"
        for e in bus.events
    )
    assert await _wait_for(lambda: sm._key("chat") not in sm._adopt_promoters)


async def test_adopted_warming_unload_stops_the_poll(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    await _adopt_into_warming(sm, container_stub)
    task = sm._adopt_promoters[sm._key("chat")]

    await sm.unload("chat")
    assert sm._key("chat") not in sm._adopt_promoters
    assert await _wait_for(task.done)

    container_stub.active.add("chat")  # even if the unit came back…
    container_stub.healthy = True
    await asyncio.sleep(0.3)
    assert sm._current_state("chat") is SlotState.OFFLINE
    assert reg.get("chat") is None


async def test_adopted_warming_poll_gives_up(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_promote: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mgr_mod, "_ADOPTED_WARMING_POLL_MAX_S", 0.2)
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await _adopt_into_warming(sm, container_stub)
    task = sm._adopt_promoters[sm._key("chat")]
    assert await _wait_for(task.done)
    assert sm._current_state("chat") is SlotState.WARMING  # watchdog backstop owns it now


async def test_normal_cold_load_warming_gets_no_health_poll(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    async def _wait_ready_timeout(port: int, timeout_s: float | None = None) -> None:
        raise TimeoutError("health wait timed out")

    container_stub.wait_ready = _wait_ready_timeout  # type: ignore[method-assign]
    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await sm.load("chat")
    assert sm._current_state("chat") is SlotState.WARMING
    assert sm._key("chat") not in sm._adopt_promoters

    container_stub.healthy = True
    await asyncio.sleep(0.3)
    assert sm._current_state("chat") is SlotState.WARMING


def _persist_stale_warming(sm: SlotManager) -> None:
    write_state_atomic(
        sm._state_file_for("chat"),
        SlotStateRecord(
            name="chat",
            state=SlotState.WARMING,
            model_id="qwen3-4b-q4_k_m",
            port=8081,
            updated_at=time.time() - 14 * 86400,  # weeks old, as filed
            message="",
            extra={},
        ),
    )


async def test_boot_with_persisted_warming_and_ready_server_promotes(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    _persist_stale_warming(SlotManager())
    container_stub.active.add("chat")
    container_stub.healthy = True

    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    assert sm._current_state("chat") is SlotState.WARMING  # precondition

    restored = await sm.reconcile_container_upstreams()

    assert restored == ["chat"]
    assert sm._current_state("chat") is SlotState.READY
    assert reg.get("chat") is not None
    assert sm._key("chat") in sm._fail_watchers


async def test_boot_with_persisted_warming_and_loading_server_recovers(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    _persist_stale_warming(SlotManager())
    container_stub.active.add("chat")
    container_stub.healthy = False

    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    await sm.reconcile_container_upstreams()

    # Adopted, not left inert: the watcher is armed, the staleness clock is
    # fresh, and the promotion poll is running.
    assert sm._current_state("chat") is SlotState.WARMING
    assert sm._key("chat") in sm._fail_watchers
    assert sm._key("chat") in sm._adopt_promoters
    rec = sm._states[sm._key("chat")]
    assert time.time() - rec.updated_at < 60
    # Not routable until the poll has seen /health answer.
    assert reg.get("chat") is None

    container_stub.healthy = True
    assert await _wait_for(lambda: sm._current_state("chat") is SlotState.READY)
    assert reg.get("chat") is not None


async def test_boot_skips_warming_slot_whose_load_is_in_flight(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    _persist_stale_warming(SlotManager())
    container_stub.active.add("chat")
    container_stub.healthy = True

    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    async with sm._lock("chat"):  # a load in this process owns the slot
        restored = await sm.reconcile_container_upstreams()
    assert restored == []
    assert sm._key("chat") not in sm._adopt_promoters


async def test_boot_offline_adopted_into_warming_is_not_routed_until_ready(
    slot_root: Path, container_stub: FakeContainerProvider, fast_promote: None
) -> None:
    container_stub.active.add("chat")
    container_stub.healthy = False

    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    restored = await sm.reconcile_container_upstreams()

    assert restored == []
    assert sm._current_state("chat") is SlotState.WARMING
    assert reg.get("chat") is None

    container_stub.healthy = True
    assert await _wait_for(lambda: sm._current_state("chat") is SlotState.READY)
    assert reg.get("chat") is not None


async def test_inconclusive_health_probe_does_not_promote(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    fast_promote: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = UpstreamRegistry()
    sm = SlotManager(upstreams_registry=reg)
    await _adopt_into_warming(sm, container_stub)

    async def _transport_error(port: int, slot_cfg: Any = None) -> dict[str, Any]:
        raise OSError("connection reset")

    monkeypatch.setattr(container_stub, "health", _transport_error)
    await asyncio.sleep(0.3)
    assert sm._current_state("chat") is SlotState.WARMING
    assert reg.get("chat") is None
