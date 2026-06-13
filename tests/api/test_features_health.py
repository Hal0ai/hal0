"""Tests for the real /api/features + /api/health/system surfaces.

Both were `{}` / `{"status":"ok","checks":{}}` stubs (health.py). Now they
return live gates + honest deep-health checks. The dashboard branches on
``features`` and reads ``status`` / ``checks`` from health.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_features_shape(client: TestClient) -> None:
    r = client.get("/api/features")
    assert r.status_code == 200, r.text
    body = r.json()
    # Every documented gate present.
    for key in ("comfyui_switchover", "memory", "memory_engine", "npu", "mcp_supervisor"):
        assert key in body, f"missing feature gate {key!r}"
    assert isinstance(body["comfyui_switchover"], bool)
    assert isinstance(body["memory"], bool)
    assert isinstance(body["memory_engine"], str)
    assert isinstance(body["npu"], bool)
    # MCP supervisor not implemented (pending ADR-0015).
    assert body["mcp_supervisor"] is False


def test_features_comfyui_gate_reads_env(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    assert client.get("/api/features").json()["comfyui_switchover"] is True
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "0")
    assert client.get("/api/features").json()["comfyui_switchover"] is False


def test_features_memory_reflects_state(client: TestClient) -> None:
    """``memory`` mirrors app.state.memory_provider (HAL0_MEMORY_ENABLED=1
    in the test env → a provider is wired)."""
    expected = getattr(client.app.state, "memory_provider", None) is not None
    assert client.get("/api/features").json()["memory"] is expected


def test_health_liveness_ok(client: TestClient) -> None:
    """Bare /api/health is a cheap 200 liveness probe.

    Regression guard: the route used to be absent (only /api/status +
    /api/health/system existed), so the installer hello, agent_shim
    readiness wait, and the systemd watchdog — all of which poll
    /api/health — got a 404 that surfaced as a false "API not responding"
    at the end of every install.
    """
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["name"] == "hal0"
    assert "version" in body


def test_health_system_ok_payload(client: TestClient) -> None:
    r = client.get("/api/health/system")
    assert r.status_code == 200, r.text  # always 200, honest payload
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    checks = body["checks"]
    # Disk + slot-manager + event-bus checks all present.
    assert "disk_state" in checks
    assert "disk_config" in checks
    assert "slot_manager" in checks
    assert "event_bus" in checks
    # With the lifespan run, slot manager + event bus are wired.
    assert checks["slot_manager"]["ok"] is True
    assert checks["event_bus"]["ok"] is True
    # Disk checks carry a numeric free_mb.
    assert isinstance(checks["disk_state"]["free_mb"], int)
