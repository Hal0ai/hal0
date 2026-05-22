"""Regression test for the stale-model_cache slot-routing bug.

A slot's GGUF can change between process start and the next request
(model swap, restart with a new config). The dispatcher's Step 2
passthrough match is keyed on ``app.state.model_cache[slot_name]``;
if that cache isn't refreshed when the slot's loaded model changes,
``POST /v1/chat/completions {"model": <new gguf>}`` matches against
whichever stale slot still advertises that filename and lands on the
wrong upstream.

The bug observed on the live box:

    slot=nano       model_cache=["Qwen3.5-0.8B-UD-Q4_K_XL.gguf"]  ← primary's CURRENT gguf
    slot=primary    model_cache=["NousResearch_Hermes-4-14B-Q5_K_M.gguf"]  ← stale

    POST /v1/chat/completions {"model": "Qwen3.5-0.8B-UD-Q4_K_XL.gguf"}
    → response.model = "Qwen3-Zro-Cdr-Reason-V2-…F16.gguf"  ← served by nano

This test pins the fix: ``_refresh_model_cache_on_ready`` subscribes
to ``slot.state`` events and re-fetches /v1/models for any slot that
transitions to ``ready``.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from hal0.api import _refresh_model_cache_on_ready
from hal0.events import EventBus
from hal0.upstreams.registry import Upstream, UpstreamRegistry


class FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


def _make_slot(name: str) -> Upstream:
    return Upstream(name=name, kind="slot", url=f"http://127.0.0.1:8000/{name}/v1", slot_name=name)


@pytest.mark.asyncio
async def test_ready_event_refreshes_stale_cache_for_that_slot() -> None:
    """slot.state ready → ``fetch_and_cache`` runs against that slot's upstream."""
    bus = EventBus()
    primary = _make_slot("primary")
    nano = _make_slot("nano")
    upstreams = FakeUpstreamRegistry([primary, nano])

    # Cache starts cross-wired (the pathological state from the live box).
    cache: dict[str, list[str]] = {
        "primary": ["stale-primary.gguf"],
        "nano": ["wrong-but-matches-primary.gguf"],
    }
    # New advertised set after the slots actually start.
    advertised: dict[str, list[str]] = {
        "primary": ["primary-current.gguf"],
        "nano": ["nano-current.gguf"],
    }

    async def fetch_and_cache(u: Upstream) -> list[str]:
        cache[u.name] = list(advertised[u.name])
        return cache[u.name]

    task = asyncio.create_task(_refresh_model_cache_on_ready(bus, upstreams, fetch_and_cache))
    try:
        # Wait for the subscriber to register before emitting.
        await asyncio.sleep(0)
        await bus.emit(
            "slot.state",
            "info",
            "slot:primary",
            "primary: starting → ready",
            data={"slot": "primary", "from": "starting", "to": "ready"},
        )
        # Yield the loop until the refresher has processed the event.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if cache["primary"] == ["primary-current.gguf"]:
                break
        assert cache["primary"] == ["primary-current.gguf"]
        # nano wasn't transitioned, its (still wrong) cache must not move.
        assert cache["nano"] == ["wrong-but-matches-primary.gguf"]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_non_ready_transitions_do_not_refresh() -> None:
    """Only the ready edge should trigger a re-fetch — starting/idle/error must not."""
    bus = EventBus()
    primary = _make_slot("primary")
    upstreams = FakeUpstreamRegistry([primary])

    cache: dict[str, list[str]] = {"primary": ["stale.gguf"]}
    fetch_calls: list[str] = []

    async def fetch_and_cache(u: Upstream) -> list[str]:
        fetch_calls.append(u.name)
        return cache[u.name]

    task = asyncio.create_task(_refresh_model_cache_on_ready(bus, upstreams, fetch_and_cache))
    try:
        await asyncio.sleep(0)
        for to_state in ("starting", "idle", "error", "offline"):
            await bus.emit(
                "slot.state",
                "info",
                "slot:primary",
                f"primary: ready → {to_state}",
                data={"slot": "primary", "from": "ready", "to": to_state},
            )
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert fetch_calls == []
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_event_for_unregistered_slot_is_ignored() -> None:
    """A slot.state for a slot with no upstream entry must not crash the task."""
    bus = EventBus()
    upstreams = FakeUpstreamRegistry([])

    fetch_calls: list[str] = []

    async def fetch_and_cache(u: Upstream) -> list[str]:
        fetch_calls.append(u.name)
        return []

    task = asyncio.create_task(_refresh_model_cache_on_ready(bus, upstreams, fetch_and_cache))
    try:
        await asyncio.sleep(0)
        await bus.emit(
            "slot.state",
            "info",
            "slot:ghost",
            "ghost: starting → ready",
            data={"slot": "ghost", "from": "starting", "to": "ready"},
        )
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert fetch_calls == []
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_fetch_exception_does_not_kill_refresher() -> None:
    """A transient /v1/models fetch failure must not stop future refreshes."""
    bus = EventBus()
    primary = _make_slot("primary")
    upstreams = FakeUpstreamRegistry([primary])

    cache: dict[str, list[str]] = {"primary": ["stale.gguf"]}
    fetch_calls: list[str] = []

    async def fetch_and_cache(u: Upstream) -> list[str]:
        fetch_calls.append(u.name)
        if len(fetch_calls) == 1:
            raise RuntimeError("simulated transient network failure")
        cache[u.name] = ["primary-current.gguf"]
        return cache[u.name]

    task = asyncio.create_task(_refresh_model_cache_on_ready(bus, upstreams, fetch_and_cache))
    try:
        await asyncio.sleep(0)
        ev_data = {"slot": "primary", "from": "starting", "to": "ready"}
        await bus.emit("slot.state", "info", "slot:primary", "1st", data=ev_data)
        await bus.emit("slot.state", "info", "slot:primary", "2nd", data=ev_data)
        for _ in range(20):
            await asyncio.sleep(0.01)
            if cache["primary"] == ["primary-current.gguf"]:
                break
        assert fetch_calls == ["primary", "primary"]
        assert cache["primary"] == ["primary-current.gguf"]
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
