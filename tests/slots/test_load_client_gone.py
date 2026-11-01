"""#2944 — an abandoned request must not start a load nobody is waiting for."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.slots import abandon
from hal0.slots.abandon import ClientGoneProbe, abandon_probe
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def _timeout(port: int, timeout_s: float | None = None) -> None:
    raise TimeoutError("health wait timed out")


class _Gone:
    """A probe whose answer the test flips."""

    def __init__(self, gone: bool = False) -> None:
        self.gone = gone

    async def __call__(self) -> bool:
        return self.gone


async def _park_warming(sm: SlotManager, stub: FakeContainerProvider) -> None:
    stub.wait_ready = _timeout  # type: ignore[method-assign]
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.WARMING
    stub.wait_ready = FakeContainerProvider.wait_ready.__get__(  # type: ignore[method-assign]
        stub, FakeContainerProvider
    )
    stub.load_calls.clear()
    stub.unload_calls.clear()


async def test_disconnected_client_starts_no_second_load_from_stale_warming(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    await _park_warming(sm, container_stub)

    token = abandon_probe.set(_Gone(True))
    try:
        slot = await sm.load("chat")
    finally:
        abandon_probe.reset(token)

    assert slot.state == SlotState.WARMING
    assert not container_stub.load_calls
    assert not container_stub.unload_calls
    assert not sm._lock("chat").locked()


async def test_live_request_still_recovers_from_stale_warming(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    await _park_warming(sm, container_stub)

    token = abandon_probe.set(_Gone(False))
    try:
        slot = await sm.load("chat")
    finally:
        abandon_probe.reset(token)

    assert slot.state == SlotState.READY
    assert container_stub.unload_calls
    assert container_stub.load_calls


async def test_client_leaving_stops_the_wait_but_the_load_finishes(
    slot_root: Path, container_stub: FakeContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(abandon, "POLL_INTERVAL_S", 0.01)
    sm = SlotManager()
    release = asyncio.Event()

    async def _slow(port: int, timeout_s: float | None = None) -> None:
        await release.wait()

    container_stub.wait_ready = _slow  # type: ignore[method-assign]
    probe = _Gone(False)
    token = abandon_probe.set(probe)
    try:
        waiting = asyncio.ensure_future(sm.load("chat"))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        probe.gone = True
        slot = await asyncio.wait_for(waiting, timeout=2)
    finally:
        abandon_probe.reset(token)

    # Wait ended while the load is still mid-flight and holding the lock.
    assert slot.state == SlotState.WARMING
    assert sm._lock("chat").locked()
    assert len(container_stub.load_calls) == 1

    release.set()
    for _ in range(100):
        if not sm._lock("chat").locked():
            break
        await asyncio.sleep(0.01)
    assert sm._current_state("chat") == SlotState.READY
    assert len(container_stub.load_calls) == 1


async def test_waiter_leaving_does_not_disturb_another_waiter(
    slot_root: Path, container_stub: FakeContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(abandon, "POLL_INTERVAL_S", 0.01)
    sm = SlotManager()
    release = asyncio.Event()

    async def _slow(port: int, timeout_s: float | None = None) -> None:
        await release.wait()

    container_stub.wait_ready = _slow  # type: ignore[method-assign]

    first = asyncio.ensure_future(sm.load("chat"))  # no probe: internal caller
    await asyncio.sleep(0.05)

    leaver_probe = _Gone(False)
    token = abandon_probe.set(leaver_probe)
    try:
        leaver = asyncio.ensure_future(sm.load("chat"))
    finally:
        abandon_probe.reset(token)
    await asyncio.sleep(0.05)
    leaver_probe.gone = True
    await asyncio.wait_for(leaver, timeout=2)

    assert not first.done()
    release.set()
    slot = await asyncio.wait_for(first, timeout=2)
    for _ in range(100):  # the leaver's detached load runs on once the lock frees
        if not sm._lock("chat").locked():
            break
        await asyncio.sleep(0.01)

    assert slot.state == SlotState.READY
    assert len(container_stub.load_calls) == 1
    assert not sm._lock("chat").locked()


async def test_no_probe_keeps_todays_behaviour(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    await _park_warming(sm, container_stub)
    slot = await sm.load("chat")
    assert slot.state == SlotState.READY
    assert container_stub.load_calls


async def test_client_gone_probe_reads_disconnect_and_ignores_timeouts() -> None:
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    class _Req:
        async def receive(self) -> dict[str, str]:
            return await queue.get()

    probe = ClientGoneProbe(_Req())
    assert await probe() is False
    await queue.put({"type": "http.request"})
    assert await probe() is False  # a body chunk is not an answer
    await queue.put({"type": "http.disconnect"})
    assert await probe() is True
    assert await probe() is True  # sticky
