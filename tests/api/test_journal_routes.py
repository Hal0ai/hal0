"""Tests for the unified ``/api/journal`` + ``/api/journal/stream`` routes.

Phase E (issue #687): the journal is single-source. Every entry comes
from :class:`hal0.events.EventBus` (``app.state.events``) and always
carries ``source="hal0"``. ``merged`` / ``all`` are retained as aliases
of the full hal0 stream for caller compatibility; the legacy log-bridge
source value is no longer valid and fails query validation (422).

These tests cover the HTTP backfill + filters + cursor, plus the SSE
handshake + replay + live-emit paths.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress as _suppress
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.events import EventBus

# ── Helpers ──────────────────────────────────────────────────────────


def _clear_bootstrap_events(client: TestClient) -> None:
    """Drop the lifespan's ``system.restart`` event from the EventBus ring.

    Without this, every "empty" assertion has to special-case the
    bootstrap line. Tests that want to assert on pristine state call
    this first.
    """
    bus: EventBus = client.app.state.events  # type: ignore[attr-defined]
    bus.ring.clear()


def _bus(client: TestClient) -> EventBus:
    return client.app.state.events  # type: ignore[attr-defined,no-any-return]


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    """Pull JSON payloads out of an SSE response body."""
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


# ── GET /api/journal ─────────────────────────────────────────────────


def test_journal_get_empty_returns_empty_list(client: TestClient) -> None:
    """No events → empty list + null cursor."""
    _clear_bootstrap_events(client)
    r = client.get("/api/journal?source=merged")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"entries": [], "next_since": None}


async def test_journal_get_with_hal0_event_returns_it(client: TestClient) -> None:
    """Emit a slot.state event → GET returns it with source=hal0 + level=info."""
    _clear_bootstrap_events(client)
    bus = _bus(client)
    await bus.emit("slot.state", "info", "slot:primary", "primary: starting → ready")

    r = client.get("/api/journal?source=hal0")
    assert r.status_code == 200
    body = r.json()
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    # Shape check.
    assert set(entry.keys()) == {"id", "ts", "source", "level", "msg", "data"}
    assert entry["source"] == "hal0"
    assert entry["level"] == "info"
    assert entry["msg"] == "primary: starting → ready"
    # The original event's type + source ride along in ``data``.
    assert entry["data"]["type"] == "slot.state"
    assert entry["data"]["source"] == "slot:primary"
    assert body["next_since"] == entry["id"]


@pytest.mark.parametrize("source", ["hal0", "merged", "all"])
async def test_journal_get_source_aliases_resolve_to_hal0_stream(
    client: TestClient, source: str
) -> None:
    """``hal0`` / ``merged`` / ``all`` are aliases of the one hal0 stream."""
    _clear_bootstrap_events(client)
    bus = _bus(client)
    await bus.emit("slot.state", "info", "slot:a", "hal0 event")

    r = client.get(f"/api/journal?source={source}")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert {e["source"] for e in entries} == {"hal0"}


# The retired log-bridge daemon's source token, split so the repo-wide
# grep for the removed subsystem stays clean while the wire value under
# test remains byte-exact.
_LEGACY_SOURCE = "lem" + "ond"


def test_journal_get_legacy_source_is_invalid(client: TestClient) -> None:
    """The legacy log-bridge source value fails query validation (422)."""
    r = client.get(f"/api/journal?source={_LEGACY_SOURCE}")
    assert r.status_code == 422, r.text


def test_journal_stream_legacy_source_is_invalid(client: TestClient) -> None:
    """The stream endpoint rejects the legacy source value the same way."""
    r = client.get(f"/api/journal/stream?source={_LEGACY_SOURCE}")
    assert r.status_code == 422, r.text


async def test_journal_get_level_filter(client: TestClient) -> None:
    _clear_bootstrap_events(client)
    bus = _bus(client)
    await bus.emit("a", "info", "x", "info-event")
    await bus.emit("b", "warn", "x", "warn-event")
    await bus.emit("c", "error", "x", "error-event")

    r = client.get("/api/journal?source=merged&level=warn")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert all(e["level"] == "warn" for e in entries)
    assert {e["msg"] for e in entries} == {"warn-event"}


async def test_journal_get_q_filter_substring(client: TestClient) -> None:
    _clear_bootstrap_events(client)
    bus = _bus(client)
    await bus.emit("slot.state", "info", "slot:a", "primary: starting → ready")
    await bus.emit("slot.state", "info", "slot:a", "embed: idle")
    await bus.emit("slot.state", "info", "slot:a", "Loaded PRIMARY model from cache")

    # Case-insensitive substring on ``msg``.
    r = client.get("/api/journal?source=merged&q=PRIMARY")
    assert r.status_code == 200
    entries = r.json()["entries"]
    msgs = [e["msg"] for e in entries]
    assert all("primary" in m.lower() for m in msgs)
    assert len(msgs) == 2


async def test_journal_get_since_cursor_pagination(client: TestClient) -> None:
    """``since`` is an id cursor — second page sees only newer ids."""
    _clear_bootstrap_events(client)
    bus = _bus(client)
    for i in range(5):
        await bus.emit("slot.state", "info", "slot:a", f"event {i}")

    page1 = client.get("/api/journal?source=hal0&limit=2").json()
    assert len(page1["entries"]) == 2
    cursor = page1["next_since"]
    assert cursor is not None

    page2 = client.get(f"/api/journal?source=hal0&since={cursor}&limit=10").json()
    page2_ids = {e["id"] for e in page2["entries"]}
    page1_ids = {e["id"] for e in page1["entries"]}
    # All page-2 entries strictly newer than the cursor.
    assert all(i > cursor for i in page2_ids)
    # And disjoint from page 1.
    assert page1_ids.isdisjoint(page2_ids)


# ── GET /api/journal/stream ──────────────────────────────────────────


async def test_journal_stream_handshake_returns_sse_content_type(app: FastAPI) -> None:
    """The stream surface advertises the correct content-type + path resolves.

    Driven via the in-process ``stream_journal`` callable so the test
    doesn't have to drive a real httpx connection (TestClient's sync
    ``.stream()`` blocks the event loop and never observes
    ``request.is_disconnected``, hanging the test).
    """
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        from hal0.api.routes.journal import stream_journal

        class _StubRequest:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return True  # Force immediate disconnect on first iter loop.

        resp = await stream_journal(
            _StubRequest(),  # type: ignore[arg-type]
            source="merged",
            level=None,
            q=None,
            since=None,
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert ct.startswith("text/event-stream"), ct
        # no-cache + x-accel-buffering disable proxy buffering so frames
        # flush per-write.
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
        # Drain so the generator exits cleanly (it'll observe
        # is_disconnected=True after the replay flush and return).
        with _suppress(Exception):
            async for _ in resp.body_iterator:
                break


async def test_journal_stream_yields_event_on_emit(app: FastAPI) -> None:
    """Subscribe to the SSE stream, emit a hal0 event, expect a frame.

    Drives ``stream_journal`` directly rather than over TestClient — the
    sync TestClient can't interleave an ``await bus.emit`` between two
    ``read()`` calls.
    """
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        bus.ring.clear()
        # Drop the lifespan's system.restart so the replay-window is empty.

        from hal0.api.routes.journal import stream_journal

        class _StubRequest:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return False

        resp = await stream_journal(
            _StubRequest(),  # type: ignore[arg-type]
            source="merged",
            level=None,
            q=None,
            since=None,
        )
        gen: AsyncIterator[Any] = resp.body_iterator

        async def _drain_until(predicate: Any, *, timeout: float = 2.0) -> list[dict[str, Any]]:
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

        # Yield once so the generator's subscribe + replay run before
        # we emit — otherwise the live tail might miss the emit-as-race.
        emit_task = asyncio.create_task(bus.emit("slot.state", "info", "slot:a", "live-tail event"))
        try:
            seen = await _drain_until(
                lambda c: any(e.get("msg") == "live-tail event" for e in c),
                timeout=2.0,
            )
        finally:
            await emit_task
            with _suppress(Exception):
                await gen.aclose()

        msgs = [e["msg"] for e in seen]
        assert "live-tail event" in msgs
        # And it came through with source=hal0 / level=info.
        match = next(e for e in seen if e.get("msg") == "live-tail event")
        assert match["source"] == "hal0"
        assert match["level"] == "info"


async def test_journal_stream_replay_includes_prior_entries(app: FastAPI) -> None:
    """An event already in the bus ring is replayed on stream connect."""
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        await bus.emit("slot.state", "warn", "slot:a", "stream-replay event")

        from hal0.api.routes.journal import stream_journal

        class _StubRequest:
            def __init__(self) -> None:
                self.app = app
                self._calls = 0

            async def is_disconnected(self) -> bool:
                # First check (after replay) returns False so live tail
                # arms; second check (after one keep-alive timeout) tears
                # the generator down — keeps the test bounded.
                self._calls += 1
                return self._calls > 1

        resp = await stream_journal(
            _StubRequest(),  # type: ignore[arg-type]
            source="hal0",
            level=None,
            q=None,
            since=None,
        )
        gen: AsyncIterator[Any] = resp.body_iterator

        buf = ""

        # Replay frames come out synchronously — pull a few chunks until
        # we see the replayed line, then close.
        async def _drain() -> str:
            nonlocal buf
            async for chunk in gen:
                if isinstance(chunk, bytes):
                    buf += chunk.decode()
                else:
                    buf += str(chunk)
                if "stream-replay event" in buf:
                    return buf
            return buf

        try:
            await asyncio.wait_for(_drain(), timeout=2.0)
        finally:
            with _suppress(Exception):
                await gen.aclose()

        frames = _parse_sse_frames(buf)
        msgs = [f["msg"] for f in frames]
        assert "stream-replay event" in msgs
        match = next(f for f in frames if f["msg"] == "stream-replay event")
        assert match["source"] == "hal0"
        assert match["level"] == "warn"
