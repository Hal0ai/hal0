"""Tests for the footer event bus + /api/events surface.

Covers:
  * ``EventBus.emit`` ↔ ring buffer + per-subscriber fan-out.
  * ``backfill`` filter primitives (since cursor, type glob, severity gate).
  * Slow-subscriber drop semantics — emit must never raise.
  * ``GET /api/events`` happy path + cursor pagination.
  * ``GET /api/events/stream`` replay then live tail via httpx AsyncClient.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import suppress as _suppress
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.events import EventBus

# ── EventBus unit tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_appends_to_ring_and_assigns_monotonic_ids() -> None:
    bus = EventBus()
    ev1 = await bus.emit("slot.state", "info", "slot:primary", "primary: offline → starting")
    ev2 = await bus.emit("pull.queued", "info", "pull:foo", "queued foo")
    assert ev1["id"] == 1
    assert ev2["id"] == 2
    assert list(bus.ring) == [ev1, ev2]
    # Required schema fields are present.
    assert set(ev1.keys()) == {"id", "ts", "type", "severity", "source", "message", "data"}
    assert ev1["data"] == {}


@pytest.mark.asyncio
async def test_ring_buffer_evicts_oldest_when_maxlen_exceeded() -> None:
    bus = EventBus(ring_maxlen=3)
    for i in range(5):
        await bus.emit("slot.state", "info", "slot:s", f"event {i}")
    ids = [ev["id"] for ev in bus.ring]
    assert ids == [3, 4, 5]


@pytest.mark.asyncio
async def test_backfill_since_cursor() -> None:
    bus = EventBus()
    for i in range(5):
        await bus.emit("slot.state", "info", "slot:s", f"event {i}")
    page = bus.backfill(since=2)
    assert [ev["id"] for ev in page] == [3, 4, 5]


@pytest.mark.asyncio
async def test_backfill_type_glob_filter() -> None:
    bus = EventBus()
    await bus.emit("slot.state", "info", "slot:a", "a")
    await bus.emit("pull.queued", "info", "pull:b", "b")
    await bus.emit("pull.progress", "info", "pull:b", "b 50%")
    await bus.emit("system.restart", "info", "system", "boot")

    pulls = bus.backfill(type_glob="pull.*")
    assert {ev["type"] for ev in pulls} == {"pull.queued", "pull.progress"}

    slots = bus.backfill(type_glob="slot.state")
    assert [ev["type"] for ev in slots] == ["slot.state"]


@pytest.mark.asyncio
async def test_backfill_min_severity_filter() -> None:
    bus = EventBus()
    await bus.emit("a", "info", "x", "1")
    await bus.emit("b", "warn", "x", "2")
    await bus.emit("c", "error", "x", "3")
    assert [ev["id"] for ev in bus.backfill(min_severity="warn")] == [2, 3]
    assert [ev["id"] for ev in bus.backfill(min_severity="error")] == [3]
    assert [ev["id"] for ev in bus.backfill(min_severity="info")] == [1, 2, 3]


@pytest.mark.asyncio
async def test_backfill_limit_keeps_most_recent() -> None:
    bus = EventBus()
    for i in range(10):
        await bus.emit("t", "info", "x", str(i))
    page = bus.backfill(limit=3)
    assert [ev["id"] for ev in page] == [8, 9, 10]


@pytest.mark.asyncio
async def test_subscribe_yields_emitted_events() -> None:
    bus = EventBus()

    async def consumer() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async with bus.subscribe() as q:
            for _ in range(3):
                out.append(await asyncio.wait_for(q.get(), timeout=1.0))
        return out

    consumer_task = asyncio.create_task(consumer())
    # Yield once so consumer's subscribe registers before emit.
    await asyncio.sleep(0)
    await bus.emit("slot.state", "info", "slot:a", "1")
    await bus.emit("slot.state", "info", "slot:a", "2")
    await bus.emit("slot.state", "info", "slot:a", "3")
    out = await consumer_task
    assert [ev["message"] for ev in out] == ["1", "2", "3"]
    # Subscriber removed on context exit.
    assert bus.subscribers == set()


@pytest.mark.asyncio
async def test_subscriber_full_queue_drops_oldest_not_raises() -> None:
    bus = EventBus(subscriber_maxsize=4)
    # Hand-register a queue that never drains so emit must overflow it.
    full_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
    bus.subscribers.add(full_q)
    try:
        for i in range(20):
            await bus.emit("t", "info", "x", str(i))  # must not raise
        # Queue still bounded.
        assert full_q.qsize() <= 4
        # Newest survives (last id == 20, ids start at 1).
        latest = None
        while not full_q.empty():
            latest = full_q.get_nowait()
        assert latest is not None
        assert latest["id"] == 20
    finally:
        bus.subscribers.discard(full_q)


# ── HTTP endpoint tests ──────────────────────────────────────────────────────


@pytest.fixture()
def app() -> FastAPI:
    return create_app()


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_get_events_returns_envelope_with_next_since(client: TestClient) -> None:
    # Lifespan emits system.restart at startup so the bus is never empty.
    r = client.get("/api/events")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["events"], list)
    assert body["events"], "expected at least the system.restart bootstrap event"
    last = body["events"][-1]
    assert body["next_since"] == last["id"]
    assert set(last.keys()) == {"id", "ts", "type", "severity", "source", "message", "data"}


@pytest.mark.asyncio
async def test_get_events_cursor_paginates(app: FastAPI) -> None:
    with TestClient(app) as client:
        bus: EventBus = client.app.state.events  # type: ignore[attr-defined]
        for i in range(5):
            await bus.emit("slot.state", "info", "slot:test", f"step {i}")
        page1 = client.get("/api/events?limit=2").json()
        assert len(page1["events"]) == 2
        cursor = page1["next_since"]
        page2 = client.get(f"/api/events?since={cursor}&limit=2").json()
        assert all(ev["id"] > cursor for ev in page2["events"])
        # No overlap.
        ids1 = {ev["id"] for ev in page1["events"]}
        ids2 = {ev["id"] for ev in page2["events"]}
        assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_get_events_type_glob(app: FastAPI) -> None:
    with TestClient(app) as client:
        bus: EventBus = client.app.state.events  # type: ignore[attr-defined]
        await bus.emit("slot.state", "info", "slot:a", "a")
        await bus.emit("pull.queued", "info", "pull:b", "b")
        await bus.emit("pull.progress", "info", "pull:b", "b 10%")
        body = client.get("/api/events?type=pull.*").json()
        types = {ev["type"] for ev in body["events"]}
        assert types == {"pull.queued", "pull.progress"}


def test_get_events_rejects_bad_severity(client: TestClient) -> None:
    r = client.get("/api/events?severity=bogus")
    # Hal0Error → 400 via the error envelope middleware.
    assert r.status_code == 400
    body = r.json()
    # Envelope shape varies — sanity check the offending value reached us.
    assert "bogus" in json.dumps(body)


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    """Pull JSON payloads out of a fragment of an SSE stream."""
    out: list[dict[str, Any]] = []
    for frame in text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                with _suppress(ValueError):
                    out.append(json.loads(payload))
    return out


@pytest.mark.asyncio
async def test_stream_replay_then_live() -> None:
    """SSE replays ring entries, then live-emits arrive after the snapshot."""
    app = create_app()
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        await bus.emit("slot.state", "info", "slot:a", "first")
        await bus.emit("slot.state", "info", "slot:a", "second")

        from hal0.api.routes.events import stream_events

        class _StubRequest:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return False

        resp = await stream_events(_StubRequest(), since=None, type=None, severity=None)  # type: ignore[arg-type]
        gen = resp.body_iterator

        # Drain the replay frames (system.restart + first + second).
        async def _drain_until(predicate: Any, *, timeout: float = 3.0) -> list[dict[str, Any]]:
            buf = ""
            collected: list[dict[str, Any]] = []
            deadline = asyncio.get_event_loop().time() + timeout
            async for chunk in gen:
                if isinstance(chunk, bytes):
                    buf += chunk.decode()
                else:
                    buf += str(chunk)
                collected = _parse_sse_frames(buf)
                if predicate(collected):
                    return collected
                if asyncio.get_event_loop().time() > deadline:
                    return collected
            return collected

        replay = await _drain_until(
            lambda c: any(ev["message"] == "second" for ev in c),
            timeout=2.0,
        )
        messages = [ev["message"] for ev in replay]
        assert "first" in messages
        assert "second" in messages

        # Emit a live event; the generator should yield it next. The
        # underlying buffer is reset per ``_drain_until`` call so we
        # only assert the live emit appears here — replay ordering was
        # already verified above.
        live_emit = asyncio.create_task(bus.emit("slot.state", "info", "slot:a", "third"))
        try:
            tail = await _drain_until(
                lambda c: any(ev["message"] == "third" for ev in c),
                timeout=2.0,
            )
        finally:
            await live_emit
            with _suppress(Exception):
                await gen.aclose()
        tail_messages = [ev["message"] for ev in tail]
        assert "third" in tail_messages
        # Replay frames are in the earlier list; the live tail's first
        # surviving frame is the new "third" event. Together they prove
        # the route does replay-then-live in that order.


@pytest.mark.asyncio
async def test_stream_since_skips_backfill() -> None:
    """``?since=<id>`` drops replay frames whose id is at or below the cursor."""
    app = create_app()
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        await bus.emit("slot.state", "info", "slot:a", "old-1")
        await bus.emit("slot.state", "info", "slot:a", "old-2")
        cursor = max(ev["id"] for ev in bus.ring)

        from hal0.api.routes.events import stream_events

        class _StubRequest:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return False

        resp = await stream_events(_StubRequest(), since=cursor, type=None, severity=None)  # type: ignore[arg-type]
        gen = resp.body_iterator

        async def _drain_one(*, timeout: float = 3.0) -> list[dict[str, Any]]:
            buf = ""
            collected: list[dict[str, Any]] = []
            deadline = asyncio.get_event_loop().time() + timeout
            async for chunk in gen:
                if isinstance(chunk, bytes):
                    buf += chunk.decode()
                else:
                    buf += str(chunk)
                collected = _parse_sse_frames(buf)
                if collected:
                    return collected
                if asyncio.get_event_loop().time() > deadline:
                    return collected
            return collected

        # Live emit so the parked queue.get() wakes up.
        emit_task = asyncio.create_task(bus.emit("slot.state", "info", "slot:a", "new"))
        try:
            collected = await _drain_one(timeout=2.0)
        finally:
            await emit_task
            with _suppress(Exception):
                await gen.aclose()

        assert collected, "expected at least one event past the cursor"
        assert all(ev["id"] > cursor for ev in collected)
        assert any(ev["message"] == "new" for ev in collected)
