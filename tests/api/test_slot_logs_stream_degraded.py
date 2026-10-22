"""``GET /api/slots/{name}/logs/stream`` when ``journalctl`` exits underneath it.

Live-box shape: ``hal0-api.service`` without ``SupplementaryGroups=
systemd-journal`` (#2435) — ``journalctl -f`` prints a permission complaint
to stderr and exits at once. The route used to end the SSE response after
zero bytes; the browser's ``EventSource`` reads stream-end as an error, so
the slot log drawer and the Logs page "slot" channel reconnected forever
("reconnecting…") and never showed a reason.

Expected now: a ``degraded`` frame carrying the reason, and then the stream
is HELD OPEN with keepalives (same discipline as the synthetic-slot branch,
#1905) so the client renders the notice instead of looping. The CLI follower
treats ``degraded`` as terminal on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.slots import logs as _logs


async def _next_frame(agen: AsyncIterator[bytes | str], timeout: float = 2.0) -> str:
    frame = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
    return frame.decode("utf-8") if isinstance(frame, bytes) else frame


@pytest.mark.asyncio
async def test_journalctl_exit_emits_degraded_then_holds_open(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_status(name: str, **_kw: object) -> None:
        return None

    # Entering the TestClient runs the lifespan that installs the slot
    # manager; the route is then driven directly so the held-open stream can
    # be consumed frame by frame (buffering the whole response would hang).
    with TestClient(app):
        await _drive(app, monkeypatch, _fake_status)


async def _drive(app: FastAPI, monkeypatch: pytest.MonkeyPatch, fake_status: object) -> None:
    monkeypatch.setattr(app.state.slot_manager, "status", fake_status)

    async def _dies(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        yield "llama_model_load: loading model"
        raise _logs.JournalTailExited(
            "hal0-api cannot read the journal (insufficient permissions) — "
            "hal0-api.service is missing SupplementaryGroups=systemd-journal",
            lines=1,
        )

    monkeypatch.setattr("hal0.slots.logs.tail_journal", _dies)
    # Hold-open cadence is read at call time so the test does not wait 15 s.
    monkeypatch.setattr(_logs, "KEEPALIVE_S", 0.01)

    from hal0.api.routes.slots import slot_logs_stream

    class _ReqShim:
        class _AppShim:
            state = app.state

        app = _AppShim()

    response = await slot_logs_stream("chat", _ReqShim())  # type: ignore[arg-type]
    assert response.status_code == 200
    agen = response.body_iterator
    try:
        first = await _next_frame(agen)
        assert first.startswith("data: "), first
        assert "loading model" in first

        second = await _next_frame(agen)
        assert second.startswith("event: degraded\n"), f"bad SSE prefix: {second!r}"
        assert "systemd-journal" in second

        # Held open: the next frames are keepalive comments, not stream end.
        third = await _next_frame(agen)
        assert third == ": keepalive\n\n", third
        fourth = await _next_frame(agen)
        assert fourth == ": keepalive\n\n", fourth
    finally:
        await agen.aclose()
