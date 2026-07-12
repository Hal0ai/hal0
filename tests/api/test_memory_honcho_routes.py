"""Tests for ``/api/memory/honcho/{stats,sync}`` (Honcho dashboard card).

Builds a bare FastAPI app with only the memory router mounted (mirrors
``tests/api/test_memory_graph_route.py``) so we exercise the routes without
a full hal0 lifespan. Honcho's own HTTP surface is faked via
``httpx.MockTransport``; systemctl calls are patched at their origin module
(``hal0.services.systemd``) since the routes import them lazily inside each
handler — no real subprocess, no real network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import tomli_w
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes

_ROUTE = "hal0.api.routes.memory"


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the config loader at a tmp HAL0_HOME with [honcho] configured."""
    home = tmp_path / "hal0-home"
    etc = home / "etc" / "hal0"
    etc.mkdir(parents=True)
    (home / "var-lib").mkdir(parents=True)
    (etc / "hal0.toml").write_text(
        tomli_w.dumps(
            {
                "meta": {"schema_version": 1},
                "honcho": {
                    "enabled": True,
                    "port": 8000,
                    "workspace": "hal0",
                    "user_peer": "operator",
                },
            }
        )
    )
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home


@pytest.fixture
def client(hal0_home: Path) -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = None
    return TestClient(app, raise_server_exceptions=False)


def _migrate_state_path(hal0_home: Path) -> Path:
    return hal0_home / "var-lib" / "honcho" / "migrate-state.json"


# ── GET /api/memory/honcho/stats ────────────────────────────────────────────


def test_honcho_stats_unreachable_is_fail_soft(client: TestClient) -> None:
    with patch(f"{_ROUTE}._probe_health", new_callable=AsyncMock, return_value=False):
        r = client.get("/api/memory/honcho/stats")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "enabled": True,
        "reachable": False,
        "version": None,
        "url": "http://127.0.0.1:8000",
        "workspace": "hal0",
        "peers": None,
        "observations": None,
        "conclusions": None,
        "deriver_pending": None,
        "deriver_processing": None,
    }


def _honcho_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/openapi.json":
            return httpx.Response(200, json={"info": {"version": "3.0.11"}})
        if path == "/v3/workspaces/hal0/peers/list":
            return httpx.Response(
                200, json={"items": [], "total": 2, "page": 1, "size": 50, "pages": 1}
            )
        if path == "/v3/workspaces/hal0/conclusions/list":
            body = request.content.decode() if request.content else "{}"
            if '"explicit"' in body:
                return httpx.Response(
                    200, json={"items": [], "total": 900, "page": 1, "size": 1, "pages": 900}
                )
            return httpx.Response(
                200, json={"items": [], "total": 1269, "page": 1, "size": 1, "pages": 1269}
            )
        if path == "/v3/workspaces/hal0/queue/status":
            return httpx.Response(
                200,
                json={
                    "total_work_units": 12,
                    "completed_work_units": 10,
                    "in_progress_work_units": 1,
                    "pending_work_units": 1,
                    "sessions": {},
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def test_honcho_stats_reachable_aggregates_counts(client: TestClient) -> None:
    real_client_cls = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _honcho_mock_transport()
        return real_client_cls(*args, **kwargs)

    with (
        patch(f"{_ROUTE}._probe_health", new_callable=AsyncMock, return_value=True),
        patch("httpx.AsyncClient", side_effect=fake_async_client),
    ):
        r = client.get("/api/memory/honcho/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["reachable"] is True
    assert body["version"] == "3.0.11"
    assert body["peers"] == 2
    # observations = explicit-level conclusions; conclusions = the rest
    # (dreamed: deductive/inductive/contradiction).
    assert body["observations"] == 900
    assert body["conclusions"] == 369
    assert body["deriver_pending"] == 1
    assert body["deriver_processing"] == 1


def test_honcho_stats_disabled_still_reports_shape(
    hal0_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (hal0_home / "etc" / "hal0" / "hal0.toml").write_text(
        tomli_w.dumps({"meta": {"schema_version": 1}, "honcho": {"enabled": False}})
    )
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    with (
        TestClient(app) as c,
        patch(f"{_ROUTE}._probe_health", new_callable=AsyncMock, return_value=False),
    ):
        r = c.get("/api/memory/honcho/stats")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["reachable"] is False


# ── GET /api/memory/honcho/sync ─────────────────────────────────────────────

_TIMER_STATE_ENABLED = {
    "active_state": "active",
    "sub_state": "waiting",
    "unit_file_state": "enabled",
    "since": "Sat 2026-07-11 21:00:00 EDT",
}
_TIMER_STATE_DISABLED = {
    "active_state": "inactive",
    "sub_state": "dead",
    "unit_file_state": "disabled",
    "since": None,
}
_TIMER_SCHEDULE = {
    "calendar": "*-*-* *:00:00",
    "last_trigger": "Sat 2026-07-11 21:00:00 EDT",
    "next_elapse": "Sat 2026-07-11 22:00:00 EDT",
}


def test_honcho_sync_status_fresh_state_is_all_none(client: TestClient, tmp_path: Path) -> None:
    from hal0.memory.honcho_migrate import MigrateState

    # The route builds its own `MigrateState()` with no args, whose default
    # ``path`` is the module-level ``DEFAULT_STATE_PATH`` constant
    # (``/var/lib/hal0/honcho/migrate-state.json``) — a hardcoded absolute
    # path, not derived from HAL0_HOME. On a host that has ever actually run
    # `hal0 memory sync-graph` (or the recurring timer), that real file
    # exists with real prior-run data, so this "fresh state" assertion
    # leaks it. Point MigrateState at an empty tmp path instead, matching
    # the pattern already used by the sibling
    # ``test_honcho_sync_status_reflects_recorded_run``/``..._failed_run``
    # tests below.
    state_path = tmp_path / "migrate-state.json"
    with (
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_DISABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
        patch("hal0.memory.honcho_migrate.MigrateState", lambda: MigrateState(state_path)),
    ):
        r = client.get("/api/memory/honcho/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["timer_enabled"] is False
    assert body["interval"] == "*-*-* *:00:00"
    assert body["next_run_at"] == "Sat 2026-07-11 22:00:00 EDT"
    assert body["last_run_at"] is None
    assert body["last_run_ok"] is None
    assert body["last_run_error"] is None
    assert body["last_synced_count"] is None


def test_honcho_sync_status_reflects_recorded_run(client: TestClient, hal0_home: Path) -> None:
    from hal0.memory.honcho_migrate import MigrateState

    state_path = _migrate_state_path(hal0_home)
    state = MigrateState(state_path)
    state.record_sync_run(ok=True, error=None, synced_count=7)
    state.save()

    with (
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_ENABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
        patch("hal0.memory.honcho_migrate.MigrateState", lambda: MigrateState(state_path)),
    ):
        r = client.get("/api/memory/honcho/sync")
    assert r.status_code == 200
    body = r.json()
    assert body["timer_enabled"] is True
    assert body["last_run_ok"] is True
    assert body["last_synced_count"] == 7
    assert body["last_run_at"] is not None


def test_honcho_sync_status_reflects_failed_run(client: TestClient, hal0_home: Path) -> None:
    from hal0.memory.honcho_migrate import MigrateState

    state_path = _migrate_state_path(hal0_home)
    state = MigrateState(state_path)
    state.record_sync_run(ok=False, error="honcho unreachable", synced_count=0)
    state.save()

    with (
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_ENABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
        patch("hal0.memory.honcho_migrate.MigrateState", lambda: MigrateState(state_path)),
    ):
        r = client.get("/api/memory/honcho/sync")
    body = r.json()
    assert body["last_run_ok"] is False
    assert body["last_run_error"] == "honcho unreachable"


# ── PUT /api/memory/honcho/sync ─────────────────────────────────────────────


def test_put_honcho_sync_requires_bool(client: TestClient) -> None:
    r = client.put("/api/memory/honcho/sync", json={"enabled": "yes"})
    assert r.status_code == 400


def test_put_honcho_sync_enable_runs_enable_then_start(client: TestClient) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_unit_action(unit: str, verb: str) -> dict[str, Any]:
        calls.append((unit, verb))
        return {"ok": True, "message": f"{verb} {unit}: ok"}

    with (
        patch("hal0.services.systemd.unit_action", side_effect=fake_unit_action),
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_ENABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
    ):
        r = client.put("/api/memory/honcho/sync", json={"enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["note"] is None
    assert body["timer_enabled"] is True
    assert calls == [
        ("hal0-honcho-sync.timer", "enable"),
        ("hal0-honcho-sync.timer", "start"),
    ]


def test_put_honcho_sync_disable_runs_stop_then_disable(client: TestClient) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_unit_action(unit: str, verb: str) -> dict[str, Any]:
        calls.append((unit, verb))
        return {"ok": True, "message": f"{verb} {unit}: ok"}

    with (
        patch("hal0.services.systemd.unit_action", side_effect=fake_unit_action),
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_DISABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
    ):
        r = client.put("/api/memory/honcho/sync", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert calls == [
        ("hal0-honcho-sync.timer", "stop"),
        ("hal0-honcho-sync.timer", "disable"),
    ]


def test_put_honcho_sync_reports_failure_without_raising(client: TestClient) -> None:
    async def fake_unit_action(unit: str, verb: str) -> dict[str, Any]:
        return {"ok": False, "message": f"{verb} {unit} failed: permission denied"}

    with (
        patch("hal0.services.systemd.unit_action", side_effect=fake_unit_action),
        patch(
            "hal0.services.systemd.unit_state",
            new_callable=AsyncMock,
            return_value=_TIMER_STATE_DISABLED,
        ),
        patch(
            "hal0.services.systemd.timer_schedule",
            new_callable=AsyncMock,
            return_value=_TIMER_SCHEDULE,
        ),
    ):
        r = client.put("/api/memory/honcho/sync", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "permission denied" in body["note"]


# ── POST /api/memory/honcho/sync/run ────────────────────────────────────────


def test_post_honcho_sync_run_starts_service_non_blocking(client: TestClient) -> None:
    async def fake_unit_action(unit: str, verb: str) -> dict[str, Any]:
        assert unit == "hal0-honcho-sync.service"
        assert verb == "start"
        return {"ok": True, "message": "start hal0-honcho-sync.service: ok"}

    with patch("hal0.services.systemd.unit_action", side_effect=fake_unit_action):
        r = client.post("/api/memory/honcho/sync/run")
    assert r.status_code == 200, r.text
    assert r.json() == {"started": True, "note": None}


def test_post_honcho_sync_run_reports_failure(client: TestClient) -> None:
    async def fake_unit_action(unit: str, verb: str) -> dict[str, Any]:
        return {"ok": False, "message": "start hal0-honcho-sync.service failed: unit not found"}

    with patch("hal0.services.systemd.unit_action", side_effect=fake_unit_action):
        r = client.post("/api/memory/honcho/sync/run")
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is False
    assert "unit not found" in body["note"]
