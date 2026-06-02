"""Unit tests for the lemond → ring journal bridge (issue #421).

Covers the two failure-mode-relevant behaviours that produced (and now
prevent) the ``Error 404: GET /logs/stream`` 1 Hz log storm:

  * frame flattening keys on lemond's ``type`` field (``logs.snapshot`` /
    ``logs.entry``), not the legacy ``op`` field;
  * :func:`hal0.journal._bridge_loop` only resets its reconnect backoff
    after a pass that produced at least one entry — an empty pass (lemond
    down / no WS log stream) keeps backing off rather than reconnecting at
    the 1 s floor.

Also pins :meth:`LemondLogRing.append` to lemond's real entry shape
(``severity`` / ``timestamp`` / ``line``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0 import journal
from hal0.journal import LemondLogRing

# ── frame flattening ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flatten_frame_reads_type_snapshot() -> None:
    frame = {
        "type": "logs.snapshot",
        "entries": [{"line": "a", "seq": 1}, {"line": "b", "seq": 2}],
    }
    out = await journal._flatten_frame(frame)
    assert [e["line"] for e in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_flatten_frame_reads_type_entry() -> None:
    frame = {"type": "logs.entry", "entry": {"line": "c", "seq": 3}}
    out = await journal._flatten_frame(frame)
    assert out == [{"line": "c", "seq": 3}]


@pytest.mark.asyncio
async def test_flatten_frame_falls_back_to_op_key() -> None:
    # Legacy/synthetic frames keyed on ``op`` still parse.
    frame = {"op": "logs.entry", "entry": {"line": "d"}}
    out = await journal._flatten_frame(frame)
    assert out == [{"line": "d"}]


# ── entry normalisation ───────────────────────────────────────────────


def test_append_reads_severity_and_timestamp_and_line() -> None:
    ring = LemondLogRing()
    stored = ring.append(
        {
            "line": "2026-06-02 [Error] (Server) boom",
            "severity": "Error",
            "timestamp": "2026-06-02 00:31:44.980",
            "seq": 7,
        }
    )
    assert stored["message"] == "2026-06-02 [Error] (Server) boom"
    assert stored["level"] == "error"
    assert stored["ts"] == "2026-06-02 00:31:44.980"


def test_append_maps_warning_severity_to_warn() -> None:
    ring = LemondLogRing()
    stored = ring.append({"line": "x", "severity": "Warning"})
    assert stored["level"] == "warn"


# ── reconnect backoff guard ───────────────────────────────────────────


class _FakeRing:
    """Records appends; lets a test assert whether a pass produced entries."""

    def __init__(self) -> None:
        self.appended: list[Any] = []

    def append(self, entry: Any) -> None:
        self.appended.append(entry)


@pytest.mark.asyncio
async def test_bridge_loop_backs_off_on_empty_passes(monkeypatch) -> None:
    """An immediately-empty stream (lemond down / no WS port) must NOT
    reset backoff to the 1 s floor — otherwise the loop reconnects at
    ~1 Hz and spams lemond with 404s (issue #421)."""
    sleeps: list[float] = []
    # Always-empty consume: stand in for stream_logs() returning nothing.
    monkeypatch.setattr(journal, "_consume_once", _consume_empty)

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        # Stop after a handful of iterations.
        if len(sleeps) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(journal.asyncio, "sleep", _fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await journal._bridge_loop(LemondLogRing())

    # 1 → 2 → 4 → 8: strictly increasing, never pinned at the floor.
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


@pytest.mark.asyncio
async def test_bridge_loop_resets_backoff_after_productive_pass(monkeypatch) -> None:
    """A pass that produced entries resets backoff to the floor so a
    genuine lemond restart reconnects fast."""
    sleeps: list[float] = []
    calls = {"n": 0}

    async def _consume(ring: LemondLogRing) -> bool:
        calls["n"] += 1
        # First pass: empty (back off). Second pass: productive (reset).
        # Third pass: empty again — should restart from the floor*2.
        if calls["n"] == 2:
            ring.append({"line": "live", "severity": "Info"})
            return True
        return False

    monkeypatch.setattr(journal, "_consume_once", _consume)

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(journal.asyncio, "sleep", _fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await journal._bridge_loop(LemondLogRing())

    # pass1 empty -> 2.0 ; pass2 productive -> reset to 1.0 ; pass3 empty -> 2.0
    assert sleeps == [2.0, 1.0, 2.0]


async def _consume_empty(ring: LemondLogRing) -> bool:
    return False
