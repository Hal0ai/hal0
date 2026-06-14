"""Regression tests for the events surface fixes (B8 epoch, B10 stream filters)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes.events import stream_events
from hal0.events import EventBus


def test_list_events_includes_epoch(client: TestClient) -> None:
    """B8: /api/events advertises a per-process epoch so a client can detect a
    restart (ids reset to 1) and rewind its cursor."""
    body = client.get("/api/events").json()
    assert "epoch" in body and isinstance(body["epoch"], str) and body["epoch"]


def test_epoch_differs_across_process_instances() -> None:
    """Two app instances (≈ two boots) carry distinct epochs."""
    a = create_app()
    b = create_app()
    with TestClient(a) as ca, TestClient(b) as cb:
        ea = ca.get("/api/events").json()["epoch"]
        eb = cb.get("/api/events").json()["epoch"]
        assert ea and eb and ea != eb


def _parse(buf: str) -> list[dict[str, Any]]:
    import json

    out = []
    for frame in buf.split("\n\n"):
        line = frame.strip()
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


@pytest.mark.asyncio
async def test_stream_severity_filter_excludes_lower() -> None:
    """B10: /api/events/stream?severity=error replays only error+ frames."""
    app = create_app()
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        await bus.emit("slot.state", "info", "slot:a", "info-msg")
        await bus.emit("slot.state", "error", "slot:a", "error-msg")

        class _Stub:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return True  # stop after backfill

        resp = await stream_events(_Stub(), since=None, type=None, severity="error")  # type: ignore[arg-type]
        buf = ""
        async for chunk in resp.body_iterator:
            buf += chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        msgs = [e["message"] for e in _parse(buf)]
        assert "error-msg" in msgs
        assert "info-msg" not in msgs


@pytest.mark.asyncio
async def test_stream_type_glob_filter() -> None:
    """B10: /api/events/stream?type=slot.* drops non-matching frames."""
    app = create_app()
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        bus: EventBus = app.state.events
        await bus.emit("slot.state", "info", "slot:a", "slot-msg")
        await bus.emit("pull.progress", "info", "pull:x", "pull-msg")

        class _Stub:
            def __init__(self) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return True

        resp = await stream_events(_Stub(), since=None, type="slot.*", severity=None)  # type: ignore[arg-type]
        buf = ""
        async for chunk in resp.body_iterator:
            buf += chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        msgs = [e["message"] for e in _parse(buf)]
        assert "slot-msg" in msgs
        assert "pull-msg" not in msgs
