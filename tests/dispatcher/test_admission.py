"""Tests for hal0.dispatcher.admission — per-slot serial admission scheduler."""

from __future__ import annotations

import asyncio

import pytest

from hal0.dispatcher.admission import (
    PRIORITY_DECISION,
    PRIORITY_DEFAULT,
    AdmissionRegistry,
    AdmissionTimeout,
    SlotAdmission,
)


@pytest.mark.asyncio
async def test_noop_for_unconfigured_slot() -> None:
    """A slot with no max_inflight configured never blocks."""
    reg = AdmissionRegistry()
    # Never calling configure() == no max_inflight set.
    async with (
        reg.acquire("brain", PRIORITY_DEFAULT, timeout_s=1.0),
        reg.acquire("brain", PRIORITY_DEFAULT, timeout_s=1.0),
    ):
        pass  # both admitted immediately, no queueing at all


@pytest.mark.asyncio
async def test_serializes_to_max_inflight_one() -> None:
    adm = SlotAdmission("flm", max_inflight=1)
    order: list[str] = []

    async def worker(name: str, hold_s: float) -> None:
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)
        order.append(f"start:{name}")
        await asyncio.sleep(hold_s)
        order.append(f"end:{name}")
        adm.release()

    t1 = asyncio.create_task(worker("a", 0.05))
    await asyncio.sleep(0.01)  # ensure a has the gate before b queues
    t2 = asyncio.create_task(worker("b", 0.0))
    await asyncio.gather(t1, t2)

    assert order == ["start:a", "end:a", "start:b", "end:b"]


@pytest.mark.asyncio
async def test_decision_priority_jumps_queued_chat() -> None:
    """A decision-class waiter is served before an already-queued chat waiter."""
    adm = SlotAdmission("flm", max_inflight=1)
    order: list[str] = []

    async def hold_gate(hold_s: float) -> None:
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)
        await asyncio.sleep(hold_s)
        adm.release()

    async def waiter(name: str, priority: int) -> None:
        await adm.acquire(priority, timeout_s=5.0)
        order.append(name)
        adm.release()

    holder = asyncio.create_task(hold_gate(0.05))
    await asyncio.sleep(0.01)  # holder owns the gate

    chat = asyncio.create_task(waiter("chat", PRIORITY_DEFAULT))
    await asyncio.sleep(0.005)  # chat enqueues first
    decision = asyncio.create_task(waiter("decision", PRIORITY_DECISION))

    await asyncio.gather(holder, chat, decision)

    assert order == ["decision", "chat"], "decision must jump the already-queued chat request"


@pytest.mark.asyncio
async def test_fifo_within_priority_class() -> None:
    adm = SlotAdmission("flm", max_inflight=1)
    order: list[str] = []

    async def hold_gate(hold_s: float) -> None:
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)
        await asyncio.sleep(hold_s)
        adm.release()

    async def waiter(name: str) -> None:
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)
        order.append(name)
        adm.release()

    holder = asyncio.create_task(hold_gate(0.05))
    await asyncio.sleep(0.01)
    w1 = asyncio.create_task(waiter("first"))
    await asyncio.sleep(0.005)
    w2 = asyncio.create_task(waiter("second"))
    await asyncio.sleep(0.005)
    w3 = asyncio.create_task(waiter("third"))

    await asyncio.gather(holder, w1, w2, w3)
    assert order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_admission_timeout_raises_and_removes_waiter() -> None:
    adm = SlotAdmission("flm", max_inflight=1)
    await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)  # occupy the only slot

    with pytest.raises(AdmissionTimeout):
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=0.05)

    assert adm.queue_depth == 0  # timed-out waiter must not linger in the queue


@pytest.mark.asyncio
async def test_release_on_error_frees_the_gate() -> None:
    adm = SlotAdmission("flm", max_inflight=1)

    async def failing() -> None:
        await adm.acquire(PRIORITY_DEFAULT, timeout_s=5.0)
        try:
            raise RuntimeError("boom")
        finally:
            adm.release()

    with pytest.raises(RuntimeError):
        await failing()

    # Gate must be free again — a second acquire does not block.
    await asyncio.wait_for(adm.acquire(PRIORITY_DEFAULT, timeout_s=0.1), timeout=0.2)
    adm.release()


@pytest.mark.asyncio
async def test_registry_status_reports_queue_depth() -> None:
    reg = AdmissionRegistry()
    reg.configure("flm", max_inflight=1)
    async with reg.acquire("flm", PRIORITY_DEFAULT, timeout_s=5.0):
        waiter = asyncio.create_task(
            asyncio.wait_for(
                reg.acquire("flm", PRIORITY_DECISION, timeout_s=5.0).__aenter__(),
                timeout=5.0,
            )
        )
        await asyncio.sleep(0.02)
        status = reg.status()
        assert status["flm"]["queue_depth"] == 1
        assert status["flm"]["queue_depth_by_priority"] == {PRIORITY_DECISION: 1}
    await waiter  # drain: it was admitted once the outer context released


@pytest.mark.asyncio
async def test_configure_none_clears_cap() -> None:
    reg = AdmissionRegistry()
    reg.configure("flm", max_inflight=1)
    reg.configure("flm", None)
    assert reg.status() == {}
    # Now unconfigured: no blocking at all.
    async with (
        reg.acquire("flm", PRIORITY_DEFAULT, timeout_s=1.0),
        reg.acquire("flm", PRIORITY_DEFAULT, timeout_s=1.0),
    ):
        pass
