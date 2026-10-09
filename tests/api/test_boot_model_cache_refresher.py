"""#2442 follow-up: the READY-event model-cache refresher starts before
slot reconciliation.

Startup reconcile can adopt a slot into WARMING, and its promotion poll fires
the READY ``slot.state`` event that the refresher keys on. The refresher used
to start only in the late ``background_tasks`` phase, so a promotion landing
during boot was never cached and ``hal0/<name>`` kept falling back.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

import hal0.api as api_mod
from hal0.events import EventBus
from hal0.upstreams.registry import Upstream, UpstreamRegistry


async def test_refresher_is_subscribed_when_start_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_prime(*_a: Any) -> None:
        return None

    monkeypatch.setattr(api_mod, "_prime_hal0_composite_cache", _noop_prime)
    fetched: list[str] = []

    async def _fetch(up: Upstream) -> list[str]:
        fetched.append(up.name)
        return []

    reg = UpstreamRegistry()
    reg.upsert(Upstream(name="chat", kind="slot", url="http://127.0.0.1:8081/v1", slot_name="chat"))
    bus = EventBus()
    ctx = api_mod.BootState()
    ctx.events = bus
    ctx.upstreams = reg
    ctx.slot_manager = types.SimpleNamespace()
    ctx.fetch_and_cache = _fetch
    ctx.model_cache = {}

    await api_mod._start_model_cache_refresher(ctx)
    try:
        # Subscribed by the time start returns: an emit right now is seen.
        assert len(bus.subscribers) == 1
        await bus.emit(
            "slot.state", "info", "slot:chat", "chat", data={"slot": "chat", "to": "ready"}
        )
        for _ in range(50):
            if fetched:
                break
            await asyncio.sleep(0.01)
        assert fetched == ["chat"]
    finally:
        await ctx.stop_refresh_task()
    assert ctx.refresh_task is not None and ctx.refresh_task.done()


async def test_slot_reconcile_phase_starts_refresher_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Started(Exception):
        pass

    async def _start(_ctx: Any) -> None:
        raise _Started

    monkeypatch.setattr(api_mod, "_start_model_cache_refresher", _start)
    with pytest.raises(_Started):
        await api_mod._boot_slot_reconcile(types.SimpleNamespace(), api_mod.BootState())


async def test_refresher_is_cancelled_when_a_later_boot_phase_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresher now starts before the lifespan's AsyncExitStack exists;
    a boot phase failing after it must not leave it subscribed."""

    class _Boom(Exception):
        pass

    async def _noop(_app: Any, _ctx: Any) -> None:
        return None

    for name in ("registries", "model_cache", "audit_store", "slot_manager", "dispatcher"):
        monkeypatch.setattr(api_mod, f"_boot_{name}", _noop)
    seen: dict[str, Any] = {}
    bus = EventBus()

    async def _reconcile(_app: Any, ctx: Any) -> None:
        ctx.events = bus
        ctx.upstreams = UpstreamRegistry()
        ctx.slot_manager = types.SimpleNamespace()
        ctx.fetch_and_cache = None
        ctx.model_cache = {}
        await api_mod._start_model_cache_refresher(ctx)
        seen["task"] = ctx.refresh_task

    async def _fail(_app: Any, _ctx: Any) -> None:
        raise _Boom

    monkeypatch.setattr(api_mod, "_boot_slot_reconcile", _reconcile)
    monkeypatch.setattr(api_mod, "_boot_model_priming", _fail)

    app = types.SimpleNamespace(state=types.SimpleNamespace())
    with pytest.raises(_Boom):
        async with api_mod.lifespan(app):  # type: ignore[arg-type]
            pass
    assert seen["task"].done()
    assert bus.subscribers == set()
