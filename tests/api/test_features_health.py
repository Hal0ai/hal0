"""Tests for the real /api/features + /api/health/system surfaces.

Both were `{}` / `{"status":"ok","checks":{}}` stubs (health.py). Now they
return live gates + honest deep-health checks. The dashboard branches on
``features`` and reads ``status`` / ``checks`` from health.
"""

from __future__ import annotations

import shutil
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient

_Usage = namedtuple("_Usage", "total used free")


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


def test_features_comfyui_switchover_always_available(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    assert client.get("/api/features").json()["comfyui_switchover"] is True
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "0")
    assert client.get("/api/features").json()["comfyui_switchover"] is True


def test_features_memory_reflects_state(client: TestClient) -> None:
    """``memory`` mirrors app.state.memory_provider ([memory].enabled
    defaults to True → a provider is wired)."""
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


def test_health_system_disk_checks_carry_total_mb(client: TestClient) -> None:
    """Both disk checks report the filesystem total beside the free figure (#3158).

    The Overview Hardware card fills its disk bar from ``total_mb`` and
    ``free_mb``; without a total the bar stayed empty.
    """
    checks = client.get("/api/health/system").json()["checks"]
    for name in ("disk_state", "disk_config"):
        disk = checks[name]
        assert isinstance(disk["total_mb"], int), name
        assert disk["total_mb"] >= disk["free_mb"] > 0, name


def test_health_system_disk_total_and_free_share_one_disk_usage_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``total_mb`` and ``free_mb`` come from one ``shutil.disk_usage`` read in MiB."""
    mib = 1024 * 1024
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(1000 * mib, 400 * mib, 600 * mib))
    disk = client.get("/api/health/system").json()["checks"]["disk_state"]
    assert disk["total_mb"] == 1000
    assert disk["free_mb"] == 600


def test_health_system_disk_unreadable_reports_zero_total(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable filesystem reports 0 free and 0 total, not a stale or invented total."""

    def _boom(_p: str) -> _Usage:
        raise OSError("no such device")

    monkeypatch.setattr(shutil, "disk_usage", _boom)
    body = client.get("/api/health/system").json()
    disk = body["checks"]["disk_state"]
    assert disk["free_mb"] == 0
    assert disk["total_mb"] == 0
    assert disk["ok"] is False
    assert body["status"] == "degraded"
