"""A failed MCP mount must be visible, not just logged and forgotten.

The fastapi-0.138 route-walker blocker went unnoticed on a live box for 21
boots because ``create_app`` catches every mount exception, emits ONE warning,
and carries on. ``/api/health`` kept saying ``ok``, ``/api/status`` kept saying
``ok``, and nothing anywhere reported that ``/mcp/admin`` and ``/mcp/memory``
did not exist. The only trace was a warning line in journald that no one greps.

The walker fix removes that particular cause; this makes the *class* of failure
observable, so the next one surfaces on the dashboard within a boot instead of
being found by hand months later.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def test_healthy_app_reports_mcp_mounted(client: TestClient) -> None:
    body = client.get("/api/health/system").json()
    assert body["checks"]["mcp_mount"]["ok"] is True
    assert set(body["checks"]["mcp_mount"]["servers"]) >= {"hal0-admin"}


def test_failed_mount_degrades_health_and_names_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount failure must flip /api/health/system to degraded and say why."""
    app = client.app
    monkeypatch.setattr(
        app.state,
        "mcp_mount_error",
        "catalog drift: classified route_id with no live route",
        raising=False,
    )
    monkeypatch.setattr(app.state, "mcp_servers", {}, raising=False)

    body = client.get("/api/health/system").json()

    assert body["status"] == "degraded"
    check = body["checks"]["mcp_mount"]
    assert check["ok"] is False
    assert "catalog drift" in check["detail"]


def test_mount_failure_is_recorded_on_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app must stash the reason, not only log it."""
    from hal0.api import mcp_mount

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("catalog drift: simulated")

    monkeypatch.setattr(mcp_mount, "mount_mcp_servers", _boom)

    from hal0.api import create_app

    app = create_app()

    assert "catalog drift: simulated" in (getattr(app.state, "mcp_mount_error", "") or "")


def test_successful_mount_leaves_no_error_recorded() -> None:
    from hal0.api import create_app

    app = create_app()
    assert not getattr(app.state, "mcp_mount_error", None)
    assert set(getattr(app.state, "mcp_servers", {}) or {}) >= {"hal0-admin"}
