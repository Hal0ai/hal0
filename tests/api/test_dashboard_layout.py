"""Tests for GET/PUT /api/user/dashboard-layout.

Uses ``tmp_hal0_home`` so the layout file lands in a tmp dir, never
touching the real /var/lib/hal0.  Mirrors the pattern in test_settings_routes.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import dashboard_layout as dashboard_layout_routes

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def layout_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """TestClient isolated under tmp_hal0_home so layout writes go to tmp.

    Mounts the dashboard-layout router at /api/user (the lead wires this in
    __init__.py for production; tests mount it directly per the project pattern).
    """
    app: FastAPI = create_app()
    app.include_router(dashboard_layout_routes.router, prefix="/api/user", tags=["user"])
    with TestClient(app) as c:
        yield c


# ── Helpers ────────────────────────────────────────────────────────────────────

_VALID_LAYOUT = {
    "v": 2,
    "order": ["slots", "memory", "throughput"],
    "enabled": {"slots": True, "memory": False, "throughput": True},
    "spans": {"slots": 6, "memory": 3, "throughput": 4},
    "pinned": [],
}


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_get_no_file_returns_empty(layout_client: TestClient) -> None:
    """GET with no saved layout returns 200 {}."""
    r = layout_client.get("/api/user/dashboard-layout")
    assert r.status_code == 200, r.text
    assert r.json() == {}


def test_put_valid_then_get_returns_layout(layout_client: TestClient) -> None:
    """PUT valid layout -> 204; subsequent GET returns it (reconciled)."""
    r = layout_client.put("/api/user/dashboard-layout", json=_VALID_LAYOUT)
    assert r.status_code == 204, r.text
    assert r.content == b""

    r2 = layout_client.get("/api/user/dashboard-layout")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["v"] == 2
    assert body["order"] == ["slots", "memory", "throughput"]
    assert body["enabled"]["slots"] is True
    assert body["enabled"]["memory"] is False
    assert body["spans"]["slots"] == 6


def test_put_unknown_card_id_in_enabled_returns_422(layout_client: TestClient) -> None:
    """PUT with an unknown card id in enabled -> 422 layout.invalid."""
    bad = dict(_VALID_LAYOUT)
    bad = {**_VALID_LAYOUT, "enabled": {"slots": True, "bogus_card": True}}
    r = layout_client.put("/api/user/dashboard-layout", json=bad)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "layout.invalid"


def test_put_unknown_key_in_order_returns_422(layout_client: TestClient) -> None:
    """PUT with an unknown (non-pin) key in order -> 422."""
    bad = {**_VALID_LAYOUT, "order": ["slots", "ghost_widget"]}
    r = layout_client.put("/api/user/dashboard-layout", json=bad)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "layout.invalid"


def test_put_wrong_version_returns_422(layout_client: TestClient) -> None:
    """PUT with v != 2 -> 422."""
    bad = {**_VALID_LAYOUT, "v": 1}
    r = layout_client.put("/api/user/dashboard-layout", json=bad)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "layout.invalid"


def test_round_trip_persistence(layout_client: TestClient) -> None:
    """Layout saved on PUT is returned on two successive GETs (persists)."""
    layout_client.put("/api/user/dashboard-layout", json=_VALID_LAYOUT)
    r1 = layout_client.get("/api/user/dashboard-layout")
    r2 = layout_client.get("/api/user/dashboard-layout")
    assert r1.json() == r2.json()
    assert r1.json()["v"] == 2


def test_reconcile_pinned_slot_gets_pin_key(layout_client: TestClient) -> None:
    """Pinned slot name gets a pin:<name> inserted into order after 'slots'."""
    layout = {
        **_VALID_LAYOUT,
        "order": ["slots", "memory"],
        "pinned": ["myslot"],
    }
    layout_client.put("/api/user/dashboard-layout", json=layout)
    r = layout_client.get("/api/user/dashboard-layout")
    body = r.json()
    assert "pin:myslot" in body["order"]
    # Must appear after "slots"
    idx_slots = body["order"].index("slots")
    idx_pin = body["order"].index("pin:myslot")
    assert idx_pin == idx_slots + 1


def test_reconcile_stale_pin_dropped(layout_client: TestClient) -> None:
    """pin:<name> key in order/spans is dropped when not in pinned and no live slot."""
    layout = {
        **_VALID_LAYOUT,
        "order": ["slots", "pin:gone_slot", "memory"],
        "spans": {"slots": 6, "pin:gone_slot": 3, "memory": 4},
        "pinned": [],  # gone_slot not pinned, no live slot named gone_slot
    }
    layout_client.put("/api/user/dashboard-layout", json=layout)
    r = layout_client.get("/api/user/dashboard-layout")
    body = r.json()
    assert "pin:gone_slot" not in body["order"]
    assert "pin:gone_slot" not in body.get("spans", {})


def test_reconcile_span_clamped(layout_client: TestClient) -> None:
    """spans values outside [1,12] are clamped on save and GET."""
    layout = {
        **_VALID_LAYOUT,
        "spans": {"slots": 99, "memory": 0, "throughput": 6},
    }
    layout_client.put("/api/user/dashboard-layout", json=layout)
    r = layout_client.get("/api/user/dashboard-layout")
    body = r.json()
    assert body["spans"]["slots"] == 12
    assert body["spans"]["memory"] == 1
    assert body["spans"]["throughput"] == 6


def test_pin_keys_in_order_allowed(layout_client: TestClient) -> None:
    """pin:<anything> keys are accepted in order (not rejected as unknown)."""
    layout = {
        **_VALID_LAYOUT,
        "order": ["slots", "pin:alpha", "memory"],
        "pinned": ["alpha"],
    }
    r = layout_client.put("/api/user/dashboard-layout", json=layout)
    assert r.status_code == 204, r.text
