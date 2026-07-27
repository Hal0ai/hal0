"""Cutover: app.state exposes memory_provider (P2)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_state_has_memory_provider(monkeypatch):
    monkeypatch.setenv("HAL0_MEMORY_ENABLED", "1")
    from hal0.api import create_app

    app = create_app()
    # New canonical name present; old name gone.
    assert hasattr(app.state, "memory_provider")
    assert not hasattr(app.state, "memory_wrapper")


def test_health_memory_enabled_reads_provider(monkeypatch):
    monkeypatch.setenv("HAL0_MEMORY_ENABLED", "1")
    from hal0.api import create_app

    with TestClient(create_app()) as client:
        body = client.get("/api/status").json()
        assert "memory_enabled" in body
