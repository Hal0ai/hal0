"""Tests for GET /api/stats/requests (dispatcher-side requests rollup).

Frozen client shape -- see ``ui/src/api/hooks/useRequestsRollup.ts`` --
plus its exposure classification (CLIENT, per the existing ``/api/stats``
prefix rule in ``hal0.security.exposure``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.routes import hardware as hardware_routes
from hal0.db.connection import connect
from hal0.db.migrate import migrate
from hal0.security.exposure import AuthClass, classify


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL0_HOME isolation -- ``connect(None)`` resolves the default db path
    through ``hal0.config.paths``, which must not touch the real
    ``/var/lib/hal0`` (no write access in CI/sandboxes)."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(hardware_routes.router, prefix="/api")
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


class _FakeWriter:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path


class _FakeMetricsService:
    def __init__(self, db_path: Path) -> None:
        self.writer = _FakeWriter(db_path)


class _FakeSingleFlight:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys

    def in_flight_keys(self) -> list[str]:
        return list(self._keys)


class _FakeDispatcher:
    def __init__(self, keys: list[str]) -> None:
        self._single_flight = _FakeSingleFlight(keys)


def test_no_metrics_service_returns_zeroed_shape(client: TestClient) -> None:
    """No app.state.metrics_service wired -- must still 200 with the frozen shape."""
    resp = client.get("/api/stats/requests")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "window_s": 60,
        "req_per_min": 0.0,
        "p50_ms": None,
        "p95_ms": None,
        "endpoints": [],
        "errors": 0,
        "dedupe": False,
    }


def test_seeded_db_shape_and_endpoints(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    now = datetime.now(UTC)
    with connect(db) as conn:
        migrate(conn)
        recent = (now - timedelta(seconds=5)).isoformat()
        conn.execute(
            "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
            "VALUES (?, 'r1', 'qwen3-4b', 1, 150.0)",
            (recent,),
        )
        conn.execute(
            "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
            "VALUES (?, 'r2', 'qwen3-4b', 0, 250.0)",
            (recent,),
        )
    app.state.metrics_service = _FakeMetricsService(db)

    resp = client.get("/api/stats/requests")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_s"] == 60
    assert body["errors"] == 1
    assert body["endpoints"] == [{"path": "qwen3-4b", "count": 2}]
    assert body["p50_ms"] is not None
    assert body["req_per_min"] == pytest.approx(2.0)


def test_custom_window_s_query_param(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    with connect(db) as conn:
        migrate(conn)
    app.state.metrics_service = _FakeMetricsService(db)

    resp = client.get("/api/stats/requests", params={"window_s": 30})
    assert resp.status_code == 200, resp.text
    assert resp.json()["window_s"] == 30


def test_dedupe_reflects_dispatcher_single_flight_state(app: FastAPI, client: TestClient) -> None:
    app.state.dispatcher = _FakeDispatcher(["upstream:model-x"])
    resp = client.get("/api/stats/requests")
    assert resp.status_code == 200, resp.text
    assert resp.json()["dedupe"] is True

    app.state.dispatcher = _FakeDispatcher([])
    resp = client.get("/api/stats/requests")
    assert resp.json()["dedupe"] is False


def test_exposure_classified_client_get() -> None:
    """/api/stats/requests matches the existing '/api/stats' CLIENT-GET rule."""
    assert classify("GET", "/api/stats/requests") is AuthClass.CLIENT
