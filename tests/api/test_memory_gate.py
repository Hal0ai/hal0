"""0.4 memory gate — ``HAL0_MEMORY_ENABLED`` toggles the whole subsystem.

The memory engine (Cognee), its MCP server (``/mcp/memory``), the REST
surface (``/api/memory/*``), and the dashboard's Agent → Memory tab ship
DISABLED by default and return in a later release. The gate lives in
``create_app`` (``src/hal0/api/__init__.py``); ``/api/status`` reports the
resulting state as ``memory_enabled`` so the SPA and backend cannot
disagree. When off, ``app.state.memory_wrapper`` is ``None`` and the REST
routes degrade to ``503`` rather than ``500``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


def _build(monkeypatch: pytest.MonkeyPatch, value: str | None) -> tuple[FastAPI, TestClient]:
    """Build a fresh app + client with HAL0_MEMORY_ENABLED set (or cleared)."""
    if value is None:
        monkeypatch.delenv("HAL0_MEMORY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("HAL0_MEMORY_ENABLED", value)
    app = create_app()
    return app, TestClient(app)


# Anything other than the literal "1" leaves memory off — including unset,
# empty, and stray truthy-looking strings. This pins the `!= "1"` contract.
@pytest.mark.parametrize("flag", [None, "0", "", "no", "true", "2"])
def test_memory_disabled_unless_flag_is_one(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    app, client = _build(monkeypatch, flag)
    # The wrapper is constructed at create_app time, before lifespan.
    assert app.state.memory_wrapper is None
    with client:
        body = client.get("/api/status").json()
        assert body["memory_enabled"] is False
        # REST surface stays mounted but reports MemoryUnavailable (503),
        # never a 500 — callers get a clean "off", not a crash.
        assert client.get("/api/memory/list").status_code == 503


def test_status_exposes_memory_enabled_as_bool(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/status always carries a boolean memory_enabled field."""
    app, client = _build(monkeypatch, "1")
    with client:
        body = client.get("/api/status").json()
        assert "memory_enabled" in body
        assert isinstance(body["memory_enabled"], bool)
        # The reported flag must mirror the real wrapper state so the field
        # is trustworthy even if Cognee fails to construct in this image.
        assert body["memory_enabled"] is (app.state.memory_wrapper is not None)
