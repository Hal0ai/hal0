"""Integration tests: mutation routes write durable audit rows.

We exercise the error path (deleting an unknown slot raises a typed error)
because it needs no slot seeding and proves the audit_action wrapper records
a truthful ``outcome=error`` and re-raises so the normal envelope still fires.
The ok path is unit-covered in tests/activity/test_store.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _activity(client: TestClient, **params):
    return client.get("/api/activity", params=params).json()["records"]


def test_delete_unknown_slot_records_error_outcome(client: TestClient) -> None:
    r = client.delete("/api/slots/__nope__")
    assert r.status_code >= 400  # typed error surfaced
    rows = _activity(client, action="slot.delete")
    assert rows, "expected a slot.delete audit row"
    row = rows[0]
    assert row["outcome"] == "error"
    assert row["severity"] == "error"
    assert row["target"] == "__nope__"
    assert row["actor"] == "dashboard"


def test_actor_from_agent_header(client: TestClient) -> None:
    client.delete("/api/slots/__nope2__", headers={"X-hal0-Agent": "claude-dev"})
    rows = _activity(client, action="slot.delete", actor="mcp:claude-dev")
    assert rows and rows[0]["target"] == "__nope2__"
