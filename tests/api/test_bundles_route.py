"""Tests for /api/bundles — first-run bundle picker REST surface (PR-17).

Covers:

  - GET /api/bundles — payload shape + tier list + eligibility filter +
    picker_pending state transitions across mark/skip writes.
  - GET /api/bundles/skip — marker write + idempotency.
  - POST /api/bundles/{name} — orchestrator side effects + npu_opt_in
    handling + body validation (unknown keys, NPU-not-shown rejection,
    case-insensitive name resolution).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.bundles import eligibility as bundle_eligibility
from hal0.bundles import store as bundle_store
from hal0.bundles import tiers as bundle_tiers


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear the module-level caches between tests so monkeypatched envs
    propagate."""

    bundle_eligibility.reset_cache()
    bundle_tiers.reset_cache()
    yield
    bundle_eligibility.reset_cache()
    bundle_tiers.reset_cache()


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


@pytest.fixture
def force_ram(monkeypatch: pytest.MonkeyPatch):
    """Helper to pin host RAM via the documented override."""

    def _set(gb: int):
        monkeypatch.setenv("HAL0_HOST_RAM_GB", str(gb))
        bundle_eligibility.reset_cache()

    return _set


# ── GET /api/bundles ──────────────────────────────────────────────────


def test_get_bundles_returns_all_five_tiers(isolated_client: TestClient, force_ram):
    force_ram(128)
    r = isolated_client.get("/api/bundles")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {t["name"] for t in body["tiers"]} == set(bundle_tiers.BUNDLES)
    assert len(body["tiers"]) == 5


def test_get_bundles_filters_eligibility_by_ram(isolated_client: TestClient, force_ram):
    force_ram(32)
    body = isolated_client.get("/api/bundles").json()
    assert body["eligible"] == ["hal0-Lite", "hal0-Default"]
    assert body["host_ram_gb"] == 32


def test_get_bundles_marks_picker_pending_when_marker_absent(
    isolated_client: TestClient, force_ram
):
    force_ram(64)
    body = isolated_client.get("/api/bundles").json()
    assert body["picker_pending"] is True
    assert body["choice"] is None


def test_get_bundles_reflects_marker_after_pick(isolated_client: TestClient, force_ram):
    force_ram(64)
    # Pre-pick via the helper so we don't depend on POST behaviour.
    bundle_store.mark_bundle_chosen("hal0-Pro", npu_opt_in=True)
    body = isolated_client.get("/api/bundles").json()
    assert body["picker_pending"] is False
    assert body["choice"]["name"] == "hal0-Pro"
    assert body["choice"]["npu_opt_in"] is True


def test_get_bundles_tier_rows_carry_total_size(isolated_client: TestClient, force_ram):
    force_ram(128)
    body = isolated_client.get("/api/bundles").json()
    pro = next(t for t in body["tiers"] if t["name"] == "hal0-Pro")
    # Pro total = 18.8 + 18.6 + (aux sums)
    assert pro["total_size_gb"] > 18.8 + 18.6


# ── GET /api/bundles/skip ─────────────────────────────────────────────


def test_skip_records_marker(isolated_client: TestClient, force_ram):
    force_ram(16)
    r = isolated_client.get("/api/bundles/skip")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["choice"]["skipped"] is True
    # And the picker is no longer pending.
    follow = isolated_client.get("/api/bundles").json()
    assert follow["picker_pending"] is False


def test_skip_is_idempotent(isolated_client: TestClient, force_ram):
    force_ram(16)
    isolated_client.get("/api/bundles/skip")
    r = isolated_client.get("/api/bundles/skip")
    assert r.status_code == 200
    body = r.json()
    assert body["choice"]["skipped"] is True


# ── POST /api/bundles/{name} ──────────────────────────────────────────


def test_select_unknown_bundle_returns_404(isolated_client: TestClient, force_ram):
    force_ram(128)
    r = isolated_client.post("/api/bundles/hal0-Gigantic", json={})
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "bundle.unknown"


def test_select_bundle_case_insensitive_name(isolated_client: TestClient, force_ram):
    force_ram(128)
    r = isolated_client.post("/api/bundles/hal0-lite", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    # Marker carries the canonical (mixed-case) name.
    assert body["choice"]["name"] == "hal0-Lite"


def test_select_bundle_writes_marker(isolated_client: TestClient, force_ram):
    force_ram(32)
    isolated_client.post("/api/bundles/hal0-Default", json={})
    body = isolated_client.get("/api/bundles").json()
    assert body["choice"]["name"] == "hal0-Default"
    assert body["picker_pending"] is False


def test_select_bundle_records_assignments(isolated_client: TestClient, force_ram):
    force_ram(32)
    r = isolated_client.post("/api/bundles/hal0-Default", json={})
    body = r.json()
    # Default ships chat.primary (no-mapping) + embed/stt/tts (mapped).
    applied = body["applied"]
    slots = {row["slot"] for row in applied}
    assert "chat.primary" in slots
    assert "embed" in slots
    assert "stt" in slots
    assert "tts" in slots


def test_select_bundle_rejects_unknown_keys(isolated_client: TestClient, force_ram):
    force_ram(128)
    r = isolated_client.post(
        "/api/bundles/hal0-Pro",
        json={"npu_opt_in": True, "extra": "no"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "bundle.unknown_fields"


def test_select_bundle_rejects_npu_optin_on_tier_without_trio(
    isolated_client: TestClient, force_ram
):
    force_ram(64)
    r = isolated_client.post("/api/bundles/hal0-Default", json={"npu_opt_in": True})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "bundle.npu_not_available"


def test_select_pro_with_npu_optin_records_flag(isolated_client: TestClient, force_ram):
    force_ram(128)
    r = isolated_client.post("/api/bundles/hal0-Pro", json={"npu_opt_in": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choice"]["npu_opt_in"] is True


def test_select_bundle_empty_body_is_accepted(isolated_client: TestClient, force_ram):
    """An empty body is the common path — picker fires POST with no body
    for tiers that don't expose NPU opt-in."""

    force_ram(128)
    r = isolated_client.post("/api/bundles/hal0-Lite")
    assert r.status_code == 200, r.text


def test_select_bundle_calls_capability_orchestrator(
    isolated_client: TestClient, force_ram, monkeypatch
):
    """Wire-level check — the route hands the manifest to the orchestrator
    and we see embed/voice/img apply calls land."""

    force_ram(32)
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _fake_apply(slot, child, body):
        calls.append((slot, child, body))
        return {
            "model": body.get("model"),
            "enabled": body.get("enabled"),
            "slot": f"{slot}-{child}",
            "status": "loading",
        }

    monkeypatch.setattr(
        isolated_client.app.state.capability_orchestrator,
        "apply",
        _fake_apply,
    )

    r = isolated_client.post("/api/bundles/hal0-Default", json={})
    assert r.status_code == 200, r.text
    keys = {(slot, child) for (slot, child, _) in calls}
    # Default seeds embed + stt + tts (chat.primary has no mapping).
    assert ("embed", "embed") in keys
    assert ("voice", "stt") in keys
    assert ("voice", "tts") in keys


def test_marker_persists_choice_after_post(isolated_client: TestClient, force_ram):
    """The marker is a real on-disk file; a fresh process under the same
    HAL0_HOME would see the picker as resolved. We assert by direct
    store read (the second client recycles tmp_hal0_home implicitly)."""

    force_ram(16)
    isolated_client.post("/api/bundles/hal0-Lite")
    choice = bundle_store.read_choice()
    assert choice is not None
    assert choice.name == "hal0-Lite"
    assert choice.skipped is False
