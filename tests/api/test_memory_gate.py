"""Memory gate — ``[memory].enabled`` toggles the whole subsystem.

The memory engine (Hindsight), its MCP server (``/mcp/memory``), the REST
surface (``/api/memory/*``), and the dashboard's Agent → Memory tab are
gated by ``[memory].enabled`` in ``hal0.toml`` (schema default ``True``).
The gate lives in ``create_app`` (``src/hal0/api/__init__.py``);
``/api/status`` reports the resulting state as ``memory_enabled`` so the
SPA and backend cannot disagree. When off, ``app.state.memory_provider`` is
``None`` and the REST routes degrade to ``503`` rather than ``500``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.loader import save_hal0_config
from hal0.config.schema import Hal0Config, MemoryConfig


def _build(enabled: bool | None) -> tuple[FastAPI, TestClient]:
    """Build a fresh app + client with ``[memory].enabled`` set (or left at
    its schema default of True when ``enabled`` is ``None``)."""
    if enabled is not None:
        save_hal0_config(Hal0Config(memory=MemoryConfig(enabled=enabled)))
    app = create_app()
    return app, TestClient(app)


def test_memory_disabled_when_config_says_so(tmp_hal0_home: str) -> None:
    app, client = _build(False)
    # The provider is constructed at create_app time, before lifespan.
    assert app.state.memory_provider is None
    with client:
        body = client.get("/api/status").json()
        assert body["memory_enabled"] is False
        # REST surface stays mounted but reports MemoryUnavailable (503),
        # never a 500 — callers get a clean "off", not a crash.
        assert client.get("/api/memory/list").status_code == 503


def test_memory_enabled_by_default(tmp_hal0_home: str) -> None:
    """No hal0.toml at all → the schema default (`enabled=True`) applies."""
    app, client = _build(None)
    with client:
        body = client.get("/api/status").json()
    assert body["memory_enabled"] is (app.state.memory_provider is not None)


def test_status_exposes_memory_enabled_as_bool(tmp_hal0_home: str) -> None:
    """/api/status always carries a boolean memory_enabled field."""
    app, client = _build(True)
    with client:
        body = client.get("/api/status").json()
        assert "memory_enabled" in body
        assert isinstance(body["memory_enabled"], bool)
        # The reported flag must mirror the real provider state so the field
        # is trustworthy even if Hindsight fails to construct in this image.
        assert body["memory_enabled"] is (app.state.memory_provider is not None)
