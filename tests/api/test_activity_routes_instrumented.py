"""Integration tests: the remaining config-mutating routes write durable audit rows.

Mirrors ``tests/api/test_activity_instrumentation.py`` (the slots exemplar): each
test drives the ERROR path of an instrumented endpoint — which needs no seeding —
and asserts a durable ``outcome=error`` row lands with the right target. This proves
the :func:`hal0.api._audit.record_action` wrapper records denied/failed actions and
re-raises so the normal error envelope still fires.

Covers: profiles (create/update/delete), capabilities (apply), approvals
(approve/deny), models (delete).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _activity(client: TestClient, **params):
    return client.get("/api/activity", params=params).json()["records"]


# ── profiles ────────────────────────────────────────────────────────────────


def test_delete_unknown_profile_records_error(client: TestClient) -> None:
    r = client.delete("/api/profiles/__nope__")
    assert r.status_code >= 400
    rows = _activity(client, action="profile.delete")
    assert rows, "expected a profile.delete audit row"
    row = rows[0]
    assert row["outcome"] == "error"
    assert row["severity"] == "error"
    assert row["target"] == "__nope__"
    assert row["actor"] == "dashboard"


def test_update_unknown_profile_records_error(client: TestClient) -> None:
    # Unknown custom profile → 404 profiles.not_found, raised inside the wrap.
    r = client.put("/api/profiles/__nope__", json={"image": "ghcr.io/x:y"})
    assert r.status_code >= 400
    rows = _activity(client, action="profile.update")
    assert rows, "expected a profile.update audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "__nope__"


def test_create_duplicate_profile_records_error(client: TestClient) -> None:
    # Re-creating a seed profile name → 409 profiles.exists, inside the wrap.
    # 'rocm' is a seed profile, so create collides.
    r = client.post(
        "/api/profiles",
        json={"name": "rocm", "image": "ghcr.io/x:y"},
    )
    assert r.status_code >= 400
    rows = _activity(client, action="profile.create")
    assert rows, "expected a profile.create audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "rocm"


def test_create_profile_records_ok(client: TestClient) -> None:
    # Happy path is cheap to seed (no slots needed) — assert outcome=ok + after.
    r = client.post(
        "/api/profiles",
        json={
            "name": "auditprof",
            "image": "ghcr.io/hal0ai/x:rocm",
            "device_class": "gpu",
        },
    )
    assert r.status_code == 201, r.text
    rows = _activity(client, action="profile.create")
    ok = [row for row in rows if row["target"] == "auditprof"]
    assert ok, "expected a profile.create ok row"
    assert ok[0]["outcome"] == "ok"
    assert ok[0]["severity"] == "ok"


# ── capabilities ─────────────────────────────────────────────────────────────


def test_apply_capability_unknown_model_records_error(client: TestClient) -> None:
    # Valid slot/child (passes route-level validation) but a bogus model →
    # orchestrator.apply raises catalog validation inside the wrap.
    r = client.post("/api/capabilities/embed/embed", json={"model": "__nope__"})
    assert r.status_code >= 400
    rows = _activity(client, action="capability.apply")
    assert rows, "expected a capability.apply audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "embed/embed"


# ── approvals ────────────────────────────────────────────────────────────────


def test_approve_unknown_approval_records_error(client: TestClient) -> None:
    r = client.post("/api/agent/approvals/__nope__/approve")
    assert r.status_code >= 400
    rows = _activity(client, action="approval.approve")
    assert rows, "expected an approval.approve audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "__nope__"


def test_deny_unknown_approval_records_error(client: TestClient) -> None:
    r = client.post("/api/agent/approvals/__nope__/deny")
    assert r.status_code >= 400
    rows = _activity(client, action="approval.deny")
    assert rows, "expected an approval.deny audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "__nope__"


# ── models ───────────────────────────────────────────────────────────────────


def test_delete_unknown_model_records_error(client: TestClient) -> None:
    r = client.delete("/api/models/__nope__")
    assert r.status_code >= 400
    rows = _activity(client, action="model.delete")
    assert rows, "expected a model.delete audit row"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["target"] == "__nope__"


def test_actor_from_agent_header_on_profile_delete(client: TestClient) -> None:
    client.delete("/api/profiles/__hdr__", headers={"X-hal0-Agent": "claude-dev"})
    rows = _activity(client, action="profile.delete", actor="mcp:claude-dev")
    assert rows and rows[0]["target"] == "__hdr__"
