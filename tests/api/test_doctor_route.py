"""Tests for GET /api/doctor -- the doctor verdict feed (D6 diagnostics panel).

Frozen client shape -- see ``ui/src/api/hooks/useDiagnoses.ts`` (Diagnosis
fields) -- plus its exposure classification (ADMIN, explicit rule in
``hal0.security.exposure``). Uses the full app (``client``/``app`` fixtures
from ``tests/conftest.py``) so the composition exercises real in-process
sibling-route calls (health/health_system/urls/capabilities/memory/services),
same as ``hal0 doctor verify --json`` does over HTTP.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.diagnostics import DIAGNOSIS_IDS
from hal0.security.exposure import AuthClass, classify


def test_shape_and_zero_slots_is_critical(client: TestClient) -> None:
    """A fresh TestClient app has zero slots -- runners row must be a
    critical fail, and the overall verdict must roll up to 'critical'
    (matches hal0.health_report.overall_status's documented precedence)."""
    resp = client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) == {"verdict", "diagnoses"}
    assert body["verdict"] in ("ok", "warn", "critical")
    assert body["verdict"] == "critical"
    assert isinstance(body["diagnoses"], list)
    assert len(body["diagnoses"]) == 7  # api, dns, runners, capabilities, memory, openwebui, hermes

    ids = {d["id"] for d in body["diagnoses"]}
    assert ids <= DIAGNOSIS_IDS
    assert "HAL0-RUNNERS-NONE-HEALTHY" in ids

    runners_row = next(d for d in body["diagnoses"] if d["id"] == "HAL0-RUNNERS-NONE-HEALTHY")
    assert runners_row["severity"] == "critical"
    assert runners_row["confidence"] == "high"

    # Every row matches the frozen Diagnosis shape (useDiagnoses.ts).
    for d in body["diagnoses"]:
        assert {"id", "severity", "confidence", "summary", "detail", "fixable"} <= set(d)
        assert isinstance(d["evidence"], list)
        assert isinstance(d["next_steps"], list)
        assert d["severity"] in ("info", "warn", "fail", "critical")
        assert d["confidence"] in ("low", "medium", "high")


def test_api_row_reports_ok_when_reachable(client: TestClient) -> None:
    """Dashboard/API is always reachable in-process (health() never fails),
    so that row must be 'info', never 'fail'/'critical'."""
    resp = client.get("/api/doctor")
    body = resp.json()
    api_row = next(d for d in body["diagnoses"] if d["id"] == "HAL0-API-UNREACHABLE")
    assert api_row["severity"] == "info"


def test_no_capability_orchestrator_degrades_not_500(app: FastAPI, client: TestClient) -> None:
    """Simulate a lifespan that never wired the orchestrator (RuntimeError
    path in get_doctor) -- must degrade to a warn row, never 500."""
    app.state.capability_orchestrator = None
    resp = client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    caps_row = next(d for d in body["diagnoses"] if d["id"] == "HAL0-CAPABILITIES-NONE")
    assert caps_row["severity"] == "warn"


def test_exposure_classified_admin_get() -> None:
    assert classify("GET", "/api/doctor") is AuthClass.ADMIN
