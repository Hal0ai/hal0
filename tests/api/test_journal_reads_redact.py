"""Every API route that returns journal content redacts it (#2435 review N3).

#2435 gives hal0-api ``SupplementaryGroups=systemd-journal``. Before it, the
journal reads below ran as plain ``hal0`` and came back empty, so their lack
of redaction leaked nothing. With the group they return real lines, and they
must scrub them the way ``/api/logs`` does:

* ``GET /api/slots/{name}/logs``      (one-shot slot tail, ``read_tail``)
* ``GET /api/comfyui/logs``           (img-slot tail)
* ``GET /api/agents/{name}/activity`` (MCP audit rows from the API journal)
* ``hal0.api.routes.mcp._read_audit_events`` (feeds ``/api/mcp/*`` audit,
  activity and per-server log views)

Free-text lines go through :func:`hal0.redaction.redact_log_line`; audit-row
``args`` go through :func:`hal0.redaction.redact_audit_args` (the same pass
the audit writer applies at write time, for rows logged before #2434).
journalctl is stubbed, so these run on any host.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_MASK = "***REDACTED***"

_SECRET_LINES = [
    "2026-10-09T10:00:00+0000 box hal0-slot-chat[1]: started",
    "2026-10-09T10:00:01+0000 box hal0-slot-chat[1]: Authorization: Bearer sk-or-LEAK-1",
    "2026-10-09T10:00:02+0000 box hal0-slot-chat[1]: env HAL0_ADMIN_KEY=abcdef1234567890",
    "2026-10-09T10:00:03+0000 box hal0-slot-chat[1]: HF_TOKEN=hf_LEAKLEAKLEAKLEAK123",
]
_LEAKED = ("sk-or-LEAK-1", "abcdef1234567890", "hf_LEAKLEAKLEAKLEAK123")


def _proc(stdout: bytes) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


def _journal_patches(stdout: bytes):
    return (
        patch("shutil.which", return_value="/usr/bin/journalctl"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc(stdout))),
    )


def _assert_masked(text: str) -> None:
    for secret in _LEAKED:
        assert secret not in text, f"leaked {secret!r}"
    assert _MASK in text


# ── free-text tails ──────────────────────────────────────────────────────────


def test_slot_logs_one_shot_redacts(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_status(name: str, **_kw: object) -> None:
        return None

    async def _fake_token(_sm: object, name: str) -> str:
        return name

    monkeypatch.setattr(app.state.slot_manager, "status", _fake_status)
    monkeypatch.setattr("hal0.api.routes.slots.slot_token_for", _fake_token)
    which, spawn = _journal_patches("\n".join(_SECRET_LINES).encode())
    with which, spawn:
        r = client.get("/api/slots/chat/logs?quiet=false")
    assert r.status_code == 200, r.text
    logs = r.json()["logs"]
    _assert_masked(logs)
    assert "started" in logs


def test_comfyui_logs_redacts(client: TestClient) -> None:
    which, spawn = _journal_patches("\n".join(_SECRET_LINES).encode())
    with which, spawn:
        r = client.get("/api/comfyui/logs?tail=60")
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert len(lines) == len(_SECRET_LINES)
    _assert_masked("\n".join(lines))
    assert lines[0] == _SECRET_LINES[0]


# ── MCP audit rows ───────────────────────────────────────────────────────────


def _audit_journal(client_id: str = "hermes") -> bytes:
    """Two pre-#2434 audit rows whose args were logged unmasked."""
    rows = [
        {
            "event": "mcp.tool.invoked",
            "client_id": client_id,
            "tool": "provider_credential_write",
            "args": {"provider": "openrouter", "value": "sk-or-LEAK-1"},
            "gated": True,
            "outcome": "executed",
            "timestamp": 1.0,
        },
        {
            "event": "mcp.tool.invoked",
            "client_id": client_id,
            "tool": "slot_logs",
            "args": {
                "note": "HAL0_ADMIN_KEY=abcdef1234567890",
                "api_key": "hf_LEAKLEAKLEAKLEAK123",
            },
            "gated": False,
            "outcome": "executed",
            "timestamp": 2.0,
        },
    ]
    return "\n".join(json.dumps({"MESSAGE": json.dumps(r)}) for r in rows).encode()


def test_agent_activity_redacts_audit_args(client: TestClient) -> None:
    which, spawn = _journal_patches(_audit_journal())
    with which, spawn:
        r = client.get("/api/agents/hermes/activity")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    _assert_masked(json.dumps(body))
    # Non-secret args survive.
    assert body["events"][0]["args"]["provider"] == "openrouter"


def test_mcp_audit_reader_redacts_args() -> None:
    from hal0.api.routes import mcp as mcp_routes

    which, spawn = _journal_patches(_audit_journal())
    with which, spawn:
        events = asyncio.run(mcp_routes._read_audit_events(limit=10))
    assert len(events) == 2
    _assert_masked(json.dumps(events))
    assert events[0]["args"]["provider"] == "openrouter"
