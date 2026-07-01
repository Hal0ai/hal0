"""#613 — /api/status must expose memory_degraded for operator visibility.

Verifies:
  1. memory_degraded=None when memory is disabled.
  2. memory_degraded=True when memory is enabled + provider is the in-memory
     PgVectorProvider fallback (degraded=True).
  3. memory_degraded=False when memory is enabled + provider is a real durable
     engine (degraded absent / False).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app


def _build(monkeypatch: pytest.MonkeyPatch, value: str | None):
    if value is None:
        monkeypatch.delenv("HAL0_MEMORY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("HAL0_MEMORY_ENABLED", value)
    app = create_app()
    return app, TestClient(app)


# ── memory_degraded field present ─────────────────────────────────────────────


def test_status_exposes_memory_degraded_field(client: TestClient) -> None:
    """/api/status always carries a memory_degraded field."""
    body = client.get("/api/status").json()
    assert "memory_degraded" in body, f"memory_degraded missing from /api/status: {body}"


# ── memory disabled → None ────────────────────────────────────────────────────


def test_status_memory_degraded_none_when_disabled(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memory_degraded=None when no memory provider is wired."""
    _app, client = _build(monkeypatch, None)
    with client:
        body = client.get("/api/status").json()
    assert body["memory_enabled"] is False
    assert body["memory_degraded"] is None


# ── in-memory fallback → True ─────────────────────────────────────────────────


def test_status_memory_degraded_true_for_pgvector_fallback(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memory_degraded=True when PgVectorProvider (in-memory fallback) is wired."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    app, client = _build(monkeypatch, "1")
    # Replace whatever provider was built with the explicit in-memory fallback.
    app.state.memory_provider = PgVectorProvider()

    with client:
        body = client.get("/api/status").json()

    assert body["memory_enabled"] is True
    assert body["memory_degraded"] is True


# ── real provider → False ─────────────────────────────────────────────────────


def test_status_memory_degraded_false_for_real_provider(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memory_degraded=False when a durable provider (no degraded attr) is wired."""
    from unittest.mock import MagicMock

    app, client = _build(monkeypatch, "1")
    # Simulate a real provider: no 'degraded' attribute (getattr fallback → False).
    fake_real_provider = MagicMock(spec=[])  # no attributes
    app.state.memory_provider = fake_real_provider

    with client:
        body = client.get("/api/status").json()

    assert body["memory_enabled"] is True
    assert body["memory_degraded"] is False
