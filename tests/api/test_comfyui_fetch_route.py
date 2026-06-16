"""Task 3.5 TDD: POST /api/comfyui/models/fetch route."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.comfyui.fetch as fetch_module
from hal0.api import create_app
from hal0.comfyui.capabilities import CAPABILITIES


@pytest.fixture
def client(tmp_hal0_home, monkeypatch):
    """Isolated TestClient with fetch_model monkeypatched."""
    call_log = []

    def fake_fetch(variant):
        job_id = f"fake-{variant.family}-{len(call_log)}"
        call_log.append((variant, job_id))
        return job_id

    monkeypatch.setattr(fetch_module, "fetch_model", fake_fetch)

    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c, call_log


def test_auto_fetch_returns_202(client):
    c, _call_log = client
    resp = c.post("/api/comfyui/models/fetch", json={"auto": True})
    assert resp.status_code == 202


def test_auto_fetch_returns_5_jobs(client):
    c, _call_log = client
    resp = c.post("/api/comfyui/models/fetch", json={"auto": True})
    data = resp.json()
    assert "jobs" in data
    assert len(data["jobs"]) == 5


def test_auto_fetch_calls_fetch_model_per_capability(client):
    c, call_log = client
    c.post("/api/comfyui/models/fetch", json={"auto": True})
    assert len(call_log) == len(CAPABILITIES)


def test_auto_fetch_job_ids_match_response(client):
    c, call_log = client
    resp = c.post("/api/comfyui/models/fetch", json={"auto": True})
    returned_ids = set(resp.json()["jobs"])
    produced_ids = {jid for _, jid in call_log}
    assert returned_ids == produced_ids


def test_explicit_selections(client):
    """Explicit selection list resolves to correct variants and calls fetch."""
    c, call_log = client
    resp = c.post(
        "/api/comfyui/models/fetch",
        json={
            "selections": [
                {"capability": "txt2img", "family": "sdxl"},
                {"capability": "txt2video", "family": "wan22"},
            ]
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert len(data["jobs"]) == 2
    families = [v.family for v, _ in call_log]
    assert "sdxl" in families
    assert "wan22" in families


def test_unknown_capability_returns_422(client):
    c, _ = client
    resp = c.post(
        "/api/comfyui/models/fetch",
        json={"selections": [{"capability": "bogus", "family": "foo"}]},
    )
    assert resp.status_code == 422


def test_unknown_family_returns_422(client):
    c, _ = client
    resp = c.post(
        "/api/comfyui/models/fetch",
        json={"selections": [{"capability": "txt2img", "family": "no-such-model"}]},
    )
    assert resp.status_code == 422


def test_missing_body_returns_422(client):
    c, _ = client
    resp = c.post("/api/comfyui/models/fetch", json={})
    assert resp.status_code == 422
