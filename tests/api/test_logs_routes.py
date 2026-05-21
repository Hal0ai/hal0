"""Tests for /api/logs and /api/logs/stream.

journalctl is rarely available in CI, so the route must degrade
gracefully — returning ``{"lines": [], "hint": "..."}`` instead of
raising. These tests cover that path plus the validation envelope.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient


def test_logs_happy_path_returns_lines_and_count(client: TestClient) -> None:
    """GET /api/logs?unit=... returns the lines+count shape.

    On hosts without journalctl the route returns an empty list plus a
    hint — still a 200, still the expected shape — so the dashboard's
    "no logs available" rendering path is exercised consistently.
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


def test_logs_stream_returns_sse_content_type(client: TestClient) -> None:
    """GET /api/logs/stream sets the SSE content-type even without journalctl.

    The TestClient buffers the streaming response in-memory; without
    journalctl the generator emits a single ``event: error`` frame and
    returns. We assert content-type + that the response body contains
    the error frame.
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
