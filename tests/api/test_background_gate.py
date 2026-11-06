"""Unit tests for the #3333 background/foreground slot gate."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from hal0.api.background_gate import BackgroundGate, get_background_gate


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_background_waits_behind_foreground() -> None:
    gate = BackgroundGate()
    order: list[str] = []

    async def foreground() -> None:
        async with gate.foreground("brain"):
            order.append("fg-start")
            await asyncio.sleep(0.05)
            order.append("fg-end")

    async def background() -> None:
        order.append("bg-wait")
        await gate.wait_background("brain", max_wait_s=5.0)
        order.append("bg-proceed")

    await asyncio.sleep(0)  # let nothing run yet
    fg_task = asyncio.create_task(foreground())
    await asyncio.sleep(0.01)  # ensure foreground has registered
    bg_task = asyncio.create_task(background())
    await asyncio.gather(fg_task, bg_task)

    assert order.index("fg-end") < order.index("bg-proceed")


@pytest.mark.asyncio
async def test_foreground_never_waits_on_background() -> None:
    gate = BackgroundGate()

    # No background API blocks foreground at all — foreground() is just a
    # head-count bracket. Entering/exiting it must never await background
    # activity, which there is none of here; the assertion is simply that it
    # completes immediately.
    async def foreground() -> str:
        async with gate.foreground("brain"):
            return "done"

    result = await asyncio.wait_for(foreground(), timeout=0.1)
    assert result == "done"


@pytest.mark.asyncio
async def test_background_proceeds_immediately_when_slot_idle() -> None:
    gate = BackgroundGate()
    waited = await asyncio.wait_for(gate.wait_background("brain", max_wait_s=5.0), timeout=0.1)
    assert waited < 0.05


@pytest.mark.asyncio
async def test_background_hits_cap_and_proceeds_anyway() -> None:
    clock = _FakeClock()
    gate = BackgroundGate(clock=clock)

    async def foreground_forever() -> None:
        async with gate.foreground("brain"):
            await asyncio.sleep(3600)

    fg_task = asyncio.create_task(foreground_forever())
    await asyncio.sleep(0.01)

    async def tick_clock() -> None:
        for _ in range(5):
            await asyncio.sleep(0.01)
            clock.advance(1.0)

    tick_task = asyncio.create_task(tick_clock())
    waited = await asyncio.wait_for(gate.wait_background("brain", max_wait_s=2.0), timeout=2.0)
    assert waited >= 2.0

    fg_task.cancel()
    tick_task.cancel()
    for t in (fg_task, tick_task):
        with contextlib.suppress(asyncio.CancelledError):
            await t


@pytest.mark.asyncio
async def test_background_releases_on_disconnect() -> None:
    gate = BackgroundGate()
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    async def foreground_forever() -> None:
        async with gate.foreground("brain"):
            await asyncio.sleep(3600)

    fg_task = asyncio.create_task(foreground_forever())
    await asyncio.sleep(0.01)

    async def flip_disconnect() -> None:
        nonlocal disconnected
        await asyncio.sleep(0.05)
        disconnected = True

    flip_task = asyncio.create_task(flip_disconnect())
    waited = await asyncio.wait_for(
        gate.wait_background("brain", max_wait_s=900.0, is_disconnected=is_disconnected),
        timeout=2.0,
    )
    assert waited < 900.0

    fg_task.cancel()
    await flip_task
    with contextlib.suppress(asyncio.CancelledError):
        await fg_task


@pytest.mark.asyncio
async def test_disjoint_slots_do_not_block_each_other() -> None:
    gate = BackgroundGate()

    async def foreground_brain() -> None:
        async with gate.foreground("brain"):
            await asyncio.sleep(3600)

    fg_task = asyncio.create_task(foreground_brain())
    await asyncio.sleep(0.01)

    waited = await asyncio.wait_for(gate.wait_background("nano", max_wait_s=5.0), timeout=0.1)
    assert waited < 0.05

    fg_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await fg_task


def test_get_background_gate_caches_on_app_state() -> None:
    class _State:
        pass

    state = _State()
    gate1 = get_background_gate(state)
    gate2 = get_background_gate(state)
    assert gate1 is gate2
    assert isinstance(gate1, BackgroundGate)


def test_get_background_gate_isolated_per_state() -> None:
    class _State:
        pass

    gate_a = get_background_gate(_State())
    gate_b = get_background_gate(_State())
    assert gate_a is not gate_b
