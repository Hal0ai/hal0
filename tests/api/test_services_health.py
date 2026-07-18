"""Tests for GET /api/services/health.

The router is not yet wired into the main app (lead mounts it in
src/hal0/api/__init__.py after the spike lands).  Tests create a
minimal FastAPI app with the router mounted under the canonical prefix
so the endpoint is reachable without touching the real app factory.

Covers:
  1. Endpoint returns 200 with all 4 service ids present.
  2. n8n is up=false, detail="unmonitored" (no real probe).
  3. With comfyui probe mocked reachable -> up=true, stat populated.
  4. comfyui probe raising -> comfyui up=false, endpoint still 200.
  5. openwebui /health 2xx -> up=true (SpikeB §5.4 real probe).
  6. openwebui unreachable / non-2xx -> up=false, honest detail.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.routes.services_health import router as services_router

_BASE = "hal0.api.routes.services_health"
_EXPECTED_IDS = {"comfyui", "hermes", "openwebui", "n8n"}


@pytest.fixture
def svc_client() -> TestClient:
    """Minimal app with only the services router mounted."""
    app = FastAPI()
    app.include_router(services_router, prefix="/api/services")
    return TestClient(app)


def _services_by_id(body: dict) -> dict:
    return {s["id"]: s for s in body["services"]}


def _stub_other_probes() -> list:
    """Patch comfyui/hermes/openwebui to neutral down states so a test can
    isolate ONE service without real network/systemd calls. Returns a list
    of started patchers the caller closes via an ExitStack, OR use as a
    context-manager group. Default: everything down/unmonitored.
    """
    return [
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch(
            f"{_BASE}._probe_openwebui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable (ConnectError)"),
        ),
    ]


# ── 1. shape: 200 + all 4 ids ─────────────────────────────────────────────────


def test_services_health_200_all_ids(svc_client: TestClient) -> None:
    with contextlib.ExitStack() as stack:
        for p in _stub_other_probes():
            stack.enter_context(p)
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200, r.text
    body = r.json()
    assert "services" in body
    assert len(body["services"]) == 4
    ids = {s["id"] for s in body["services"]}
    assert ids == _EXPECTED_IDS


# ── 2. n8n always unmonitored (no real probe) ─────────────────────────────────


def test_n8n_is_unmonitored(svc_client: TestClient) -> None:
    with contextlib.ExitStack() as stack:
        for p in _stub_other_probes():
            stack.enter_context(p)
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    n8n = _services_by_id(r.json())["n8n"]
    assert n8n["up"] is False
    assert n8n["detail"] == "unmonitored"
    assert n8n["stat"] is None


# ── 3. comfyui reachable -> up=true, stat populated ───────────────────────────


def test_comfyui_reachable_up_true_stat_populated(svc_client: TestClient) -> None:
    mock_stat = {"label": "jobs", "value": "2 running / 3 queued"}
    with (
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(True, "running — 2 job(s) active", mock_stat, "http://127.0.0.1:8188"),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch(
            f"{_BASE}._probe_openwebui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable (ConnectError)"),
        ),
    ):
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    svcs = _services_by_id(r.json())
    cu = svcs["comfyui"]
    assert cu["up"] is True
    assert cu["stat"] is not None
    assert cu["stat"]["label"] == "jobs"
    assert cu["url"] == "http://127.0.0.1:8188"


# ── 4. comfyui probe raises -> up=false, endpoint still 200 ───────────────────


def test_comfyui_probe_raises_degrades_gracefully(svc_client: TestClient) -> None:
    with (
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection reset"),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch(
            f"{_BASE}._probe_openwebui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable (ConnectError)"),
        ),
    ):
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    svcs = _services_by_id(r.json())
    cu = svcs["comfyui"]
    assert cu["up"] is False
    assert cu["detail"] == "RuntimeError"
    assert cu["stat"] is None


# ── 5/6. openwebui real /health probe (SpikeB §5.4) ───────────────────────────
# Exercise the REAL _probe_openwebui by mocking httpx.AsyncClient.get at the
# transport boundary, so the status-code branching + exception handling run.


def test_openwebui_health_2xx_up_true(svc_client: TestClient) -> None:
    ok_resp = httpx.Response(200, request=httpx.Request("GET", "http://x/health"))
    with (
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_resp),
    ):
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    ow = _services_by_id(r.json())["openwebui"]
    assert ow["up"] is True
    assert "ok" in ow["detail"]


def test_openwebui_unreachable_up_false(svc_client: TestClient) -> None:
    conn_err = httpx.ConnectError("conn refused", request=httpx.Request("GET", "http://x/health"))
    with (
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=conn_err),
    ):
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    ow = _services_by_id(r.json())["openwebui"]
    assert ow["up"] is False
    assert "unreachable" in ow["detail"]


def test_openwebui_non_2xx_up_false(svc_client: TestClient) -> None:
    bad_resp = httpx.Response(503, request=httpx.Request("GET", "http://x/health"))
    with (
        patch(
            f"{_BASE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            f"{_BASE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=bad_resp),
    ):
        r = svc_client.get("/api/services/health")

    assert r.status_code == 200
    ow = _services_by_id(r.json())["openwebui"]
    assert ow["up"] is False
    assert "503" in ow["detail"]
