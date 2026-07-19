"""Tests for GET /api/auth/exposure -- serializes RULES + OPEN_ALLOWLIST.

Backs the Settings ▸ Security page's live exposure table
(``ui/src/dash/settings/pages/server/ExposureTable.jsx``, currently a
stub-with-reason). No frozen client shape yet (the UI doesn't consume this
route today) -- these tests pin the response against the live
``hal0.security.exposure`` table instead, so a widening/narrowing PR to
RULES shows up here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.security.exposure import OPEN_ALLOWLIST, RULES, AuthClass, classify


def test_shape_and_class_completeness(client: TestClient) -> None:
    resp = client.get("/api/auth/exposure")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) == {"classes", "rules", "open_allowlist"}
    assert set(body["classes"]) == {"open", "client", "admin", "bootstrap"}
    assert len(body["rules"]) == len(RULES)

    for rule in body["rules"]:
        assert set(rule) == {"label", "auth_class", "methods", "pattern", "kind"}
        assert rule["auth_class"] in body["classes"]
        assert rule["kind"] in ("exact", "prefix", "catchall")


def test_open_allowlist_matches_the_live_table(client: TestClient) -> None:
    resp = client.get("/api/auth/exposure")
    body = resp.json()
    served = {(e["method"], e["path"]) for e in body["open_allowlist"]}
    assert served == OPEN_ALLOWLIST


def test_this_route_itself_is_admin_and_present_in_the_table(client: TestClient) -> None:
    """The route can't classify itself OPEN — it would defeat the point."""
    resp = client.get("/api/auth/exposure")
    body = resp.json()
    matches = [
        r for r in body["rules"] if r["kind"] == "exact" and r["pattern"] == "/api/auth/exposure"
    ]
    assert matches, "no rule found for /api/auth/exposure in the served table"
    assert all(r["auth_class"] == "admin" for r in matches)


def test_exposure_classified_admin_get() -> None:
    assert classify("GET", "/api/auth/exposure") is AuthClass.ADMIN
