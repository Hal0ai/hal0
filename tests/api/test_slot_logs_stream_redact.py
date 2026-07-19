"""Regression test for the slot-scoped SSE log stream's secret redaction
(lane/slotlogs-redact).

``GET /api/slots/{name}/logs/stream`` has its own independent journalctl
plumbing in :mod:`hal0.slots.logs` (``tail_journal``) — separate from
``hal0.api.routes.logs.journalctl_sse`` (the ``/api/logs`` and
``/api/logs/stream`` generator fixed by the earlier api-logs-redact lane).
That independence meant this route kept streaming raw journal lines —
Bearer tokens, ``HAL0_BEARER_TOKEN=`` env prints, hal0's own
``*_KEY=``-shaped admin/client credentials — with zero redaction, a THIRD
leak path flagged (but left out of scope) by api-logs-redact.

This test drives the real route handler end-to-end (no HTTP-level auth
bypass, no route rewiring) with ``hal0.slots.logs.tail_journal`` swapped
for a fake async generator that yields secret-bearing lines, and the
SlotManager's ``status`` swapped for a no-op so the 404-existence check
passes without a real slot on disk. It asserts every leaked secret is
scrubbed from the SSE body and the shared ``***REDACTED***`` sentinel
appears — proving the route now uses
:func:`hal0.api._redact.redact_log_line`, the same helper ``/api/logs``
and ``/api/logs/stream`` already share.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SECRET_LINES = [
    "[00:00] hal0-slot@chat.service started",
    "[00:01] outbound Authorization: Bearer sk-or-LEAK-1 to provider",
    "[00:02] env dump: HAL0_ADMIN_KEY=abcdef1234567890",
    "[00:03] mcp.tool.invoked client_id=abcdefghijklmnopqrstuvwxyz0123456789 tool=slot_list",
]

_LEAKED_SECRETS = (
    "sk-or-LEAK-1",
    "abcdef1234567890",
    "abcdefghijklmnopqrstuvwxyz0123456789",
)


def test_slot_logs_stream_redacts_secret_bearing_lines(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/slots/{name}/logs/stream never emits a Bearer / client_id= /
    *_KEY= secret in any SSE frame."""

    async def _fake_status(name: str, **_kw: object) -> None:
        # The route only calls this to 404-fast on an unknown slot; the
        # return value is discarded, so a no-op stand-in is enough.
        return None

    monkeypatch.setattr(app.state.slot_manager, "status", _fake_status)

    async def _fake_tail_journal(
        unit: str, backfill_n: int = 0, quiet: bool = True
    ) -> AsyncIterator[str]:
        for line in _SECRET_LINES:
            yield line

    monkeypatch.setattr("hal0.slots.logs.tail_journal", _fake_tail_journal)

    r = client.get("/api/slots/chat/logs/stream")
    assert r.status_code == 200
    body = r.text
    for secret in _LEAKED_SECRETS:
        assert secret not in body, f"leaked secret {secret!r} in slot log stream body"
    assert "***REDACTED***" in body
    # Non-secret content survives untouched.
    assert "hal0-slot@chat.service started" in body
