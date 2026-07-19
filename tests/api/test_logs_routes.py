"""Tests for /api/logs and /api/logs/stream.

journalctl is rarely available in CI, so the route must degrade
gracefully — returning ``{"lines": [], "hint": "..."}`` instead of
raising. These tests cover that path plus the validation envelope.

The redaction tests (api-logs-redact) mock ``asyncio.create_subprocess_exec``
directly so they exercise the real redaction wiring without depending on
a real journalctl binary or actual secrets on the test host.
"""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.systemd
def test_logs_happy_path_returns_lines_and_count(client: TestClient) -> None:
    """GET /api/logs?unit=... returns the lines+count shape.

    On hosts without journalctl the route returns an empty list plus a
    hint — still a 200, still the expected shape — so the dashboard's
    "no logs available" rendering path is exercised consistently.

    P4-tests: whenever journalctl IS on PATH the route shells out to it
    for real (see ``hal0.api.routes.logs``) — genuinely systemd-dependent,
    not mocked. Marked so a local capped verify can skip a slow/misbehaving
    local journalctl with ``-m "not systemd"``.
    """
    r = client.get("/api/logs", params={"unit": "hal0-api"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit"] == "hal0-api"
    assert "lines" in body and isinstance(body["lines"], list)
    assert "count" in body and isinstance(body["count"], int)
    if shutil.which("journalctl") is None:
        assert body.get("hint"), "expected a hint on hosts without journalctl"


def test_logs_validation_error_envelope_for_missing_unit(client: TestClient) -> None:
    """Missing unit query param yields a 422 in the hal0 envelope shape.

    The ``RequestValidationError`` handler (see
    ``hal0.api.middleware.error_codes``) reshapes FastAPI's default
    ``{"detail": [...]}`` 422 into the canonical envelope with
    ``code="validation.invalid"`` while preserving the FastAPI-default
    422 status — clients already expect 422 on request-validation
    failures.
    """
    r = client.get("/api/logs")
    assert r.status_code == 422
    body = r.json()
    assert "error" in body, f"Expected hal0 envelope, got {body}"
    assert body["error"]["code"] == "validation.invalid"
    assert "fields" in body["error"]["details"]
    assert isinstance(body["error"]["details"]["fields"], list)
    assert body["error"]["details"]["fields"], "expected at least one field entry"


def test_logs_invalid_unit_returns_typed_envelope(client: TestClient) -> None:
    """A shell-special char in unit name rejects with the typed envelope."""
    r = client.get("/api/logs", params={"unit": "hal0; rm -rf /"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "system.logs_error"
    assert "details" in body["error"]


def test_logs_invalid_level_returns_typed_envelope(client: TestClient) -> None:
    """An unknown ?level= value returns the typed logs error envelope."""
    r = client.get("/api/logs", params={"unit": "hal0-api", "level": "spicy"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "system.logs_error"
    assert "allowed" in body["error"]["details"]


def test_logs_n_out_of_range_returns_envelope(client: TestClient) -> None:
    """?n=0 is below the validator floor and yields the hal0 envelope.

    Pydantic-driven validation is reshaped by the ``RequestValidationError``
    handler in ``hal0.api.middleware.error_codes`` into the canonical
    envelope with ``code="validation.invalid"`` at the FastAPI-default
    422 status.
    """
    r = client.get("/api/logs", params={"unit": "hal0-api", "n": 0})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    assert "fields" in body["error"]["details"]


@pytest.mark.systemd
def test_logs_stream_returns_sse_content_type(client: TestClient) -> None:
    """GET /api/logs/stream sets the SSE content-type even without journalctl.

    The TestClient buffers the streaming response in-memory; without
    journalctl the generator emits a single ``event: error`` frame and
    returns. We assert content-type + that the response body contains
    the error frame.

    P4-tests: this test's own precondition is "journalctl absent" (the
    presence check below skips it otherwise, since `-f` would block on
    follow) — still fundamentally systemd/journald-coupled, so it carries
    the marker for local capped-verify exclusion alongside the other
    real-journalctl test in this file.
    """
    if shutil.which("journalctl") is not None:
        pytest.skip("journalctl is installed; the stream would block on follow")
    r = client.get("/api/logs/stream", params={"unit": "hal0-api"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert "event: error" in r.text


def test_logs_stream_invalid_unit_rejects(client: TestClient) -> None:
    """Validation runs before the SSE generator starts."""
    r = client.get("/api/logs/stream", params={"unit": "bad name with space"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "system.logs_error"


# ── Secret redaction (api-logs-redact) ───────────────────────────────────────
#
# routes/logs.py streamed raw journalctl output with zero redaction — a
# leak path independent of the MCP client_id fix (SEC-mcp-clientid). Both
# endpoints must now scrub Bearer / client_id= / *_KEY= secrets before a
# journal line reaches a client. journalctl itself is mocked out via
# asyncio.create_subprocess_exec so these tests run without systemd.

_SECRET_LINES = [
    "[00:00] hal0.api.startup version=0.2.0a2",
    "[00:01] outbound Authorization: Bearer sk-or-LEAK-1 to provider",
    "[00:02] env dump: HAL0_ADMIN_KEY=abcdef1234567890",
    "[00:03] mcp.tool.invoked client_id=abcdefghijklmnopqrstuvwxyz0123456789 tool=slot_list",
]

_LEAKED_SECRETS = (
    "sk-or-LEAK-1",
    "abcdef1234567890",
    "abcdefghijklmnopqrstuvwxyz0123456789",
)


def _make_oneshot_proc(stdout: bytes) -> MagicMock:
    """Fake asyncio.Process for the one-shot `journalctl -n N` call."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _make_streaming_proc(lines: list[bytes]) -> MagicMock:
    """Fake asyncio.Process for the follow (`journalctl -f`) call.

    ``proc.stdout`` is an async generator so ``async for raw in proc.stdout``
    in journalctl_sse() iterates it exactly like a real StreamReader, then
    exits the loop naturally (no infinite follow, no CancelledError needed).
    """

    async def _stdout_iter():
        for line in lines:
            yield line

    proc = MagicMock()
    proc.stdout = _stdout_iter()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def test_logs_list_redacts_secret_bearing_lines(client: TestClient) -> None:
    """GET /api/logs never echoes a Bearer / client_id= / *_KEY= secret."""
    stdout = ("\n".join(_SECRET_LINES) + "\n").encode()
    proc = _make_oneshot_proc(stdout)
    with (
        patch("shutil.which", return_value="/usr/bin/journalctl"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        r = client.get("/api/logs", params={"unit": "hal0-api"})
    assert r.status_code == 200, r.text
    body = r.json()
    text = str(body)
    for secret in _LEAKED_SECRETS:
        assert secret not in text, f"leaked secret {secret!r} in {text!r}"
    assert "***REDACTED***" in text
    # Non-secret content survives untouched.
    assert any("hal0.api.startup" in line for line in body["lines"])


def test_logs_stream_redacts_secret_bearing_lines(client: TestClient) -> None:
    """GET /api/logs/stream never emits a Bearer / client_id= / *_KEY= secret
    in any SSE frame."""
    proc = _make_streaming_proc([f"{line}\n".encode() for line in _SECRET_LINES])
    with (
        patch("shutil.which", return_value="/usr/bin/journalctl"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        r = client.get("/api/logs/stream", params={"unit": "hal0-api"})
    assert r.status_code == 200
    body = r.text
    for secret in _LEAKED_SECRETS:
        assert secret not in body, f"leaked secret {secret!r} in stream body"
    assert "***REDACTED***" in body
    assert "hal0.api.startup" in body
