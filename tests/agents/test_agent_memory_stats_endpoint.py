"""HTTP tests for ``GET /api/agents/{agent_id}/memory/stats`` (v0.3 PR-11).

Pins the sidebar memory chip contract — the dashboard renders the
``writes`` / ``last_write`` / ``available`` fields verbatim and falls
back to the "memory not configured" hint when ``available=false``.

The route reads through ``request.app.state.memory_wrapper`` so tests
swap a fake wrapper onto app.state and assert the resulting shape.

Test matrix:

* Unknown agent → 404
* No wrapper on app.state → ``available=false``, zeros, ``reason`` set
* Wrapper present, empty namespace → ``available=true``, ``writes=0``,
  ``last_write=None``
* Wrapper present, items returned → ``available=true``, ``writes=N``,
  ``last_write`` resolves from the first item's timestamp (DESC order)
* Wrapper raises → ``available=false``, ``reason`` set, no 500
* Both integer and string timestamp shapes are tolerated
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient


class _FakeWrapper:
    """Records list_items calls and returns canned payloads."""

    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self.payload = payload or {"items": [], "next_cursor": None}
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def list_items(
        self,
        dataset: str = "shared",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self.calls.append({"dataset": dataset, "cursor": cursor, "limit": limit})
        if self.exc is not None:
            raise self.exc
        return self.payload


@pytest.fixture
def fake_wrapper_empty(client: TestClient) -> _FakeWrapper:
    fake = _FakeWrapper({"items": [], "next_cursor": None})
    client.app.state.memory_wrapper = fake
    return fake


def test_memory_stats_unknown_agent_returns_404(client: TestClient) -> None:
    r = client.get("/api/agents/pi-coder/memory/stats")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


def test_memory_stats_no_wrapper_returns_available_false(client: TestClient) -> None:
    # Default app state has no memory wrapper (init failed on the test
    # host because /var/lib/hal0 isn't writable). The endpoint must
    # render an ``available=false`` envelope rather than 500.
    client.app.state.memory_wrapper = None
    r = client.get("/api/agents/hermes/memory/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["namespace"] == "private:hermes"
    assert body["writes"] == 0
    assert body["reads"] == 0
    assert body["last_write"] is None
    assert body["available"] is False
    assert "reason" in body


def test_memory_stats_empty_namespace_returns_zero(
    client: TestClient, fake_wrapper_empty: _FakeWrapper
) -> None:
    r = client.get("/api/agents/hermes/memory/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["writes"] == 0
    assert body["last_write"] is None
    assert body["available"] is True
    # Wrapper was queried with the per-agent private namespace, NOT
    # ``shared`` — the chip is per-agent, not global.
    assert len(fake_wrapper_empty.calls) == 1
    assert fake_wrapper_empty.calls[0]["dataset"] == "private:hermes"


def test_memory_stats_with_items_reports_count_and_last_write(client: TestClient) -> None:
    fake = _FakeWrapper(
        {
            "items": [
                {"id": "m1", "timestamp": "2026-05-28T12:34:56+00:00", "text": "newest"},
                {"id": "m2", "timestamp": "2026-05-27T01:23:45+00:00", "text": "older"},
                {"id": "m3", "timestamp": "2026-05-26T00:00:00+00:00", "text": "oldest"},
            ],
            "next_cursor": None,
        }
    )
    client.app.state.memory_wrapper = fake

    r = client.get("/api/agents/hermes/memory/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["writes"] == 3
    assert body["last_write"] == "2026-05-28T12:34:56+00:00"
    assert body["available"] is True


def test_memory_stats_with_int_timestamp_converts_to_iso(client: TestClient) -> None:
    """Cognee's raw int seconds-since-epoch timestamps get rendered as ISO."""
    fake = _FakeWrapper(
        {
            "items": [
                {"id": "m1", "timestamp": 1716903296, "text": "newest"},
            ],
            "next_cursor": None,
        }
    )
    client.app.state.memory_wrapper = fake

    r = client.get("/api/agents/hermes/memory/stats")
    body = r.json()
    assert body["writes"] == 1
    # The exact ISO format isn't pinned, but it has to be parseable
    # back to the same epoch second.
    parsed = datetime.fromisoformat(body["last_write"])
    assert parsed.tzinfo is not None
    assert int(parsed.astimezone(UTC).timestamp()) == 1716903296


def test_memory_stats_wrapper_raises_returns_available_false(client: TestClient) -> None:
    fake = _FakeWrapper(exc=RuntimeError("simulated wrapper failure"))
    client.app.state.memory_wrapper = fake

    r = client.get("/api/agents/hermes/memory/stats")
    # Still 200 — the sidebar chip can't degrade if this 500s.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["writes"] == 0
    assert "reason" in body


def test_memory_stats_namespace_is_per_agent_private(client: TestClient) -> None:
    """Sidebar reflects what THIS agent has done; ``shared`` would muddy."""
    fake = _FakeWrapper()
    client.app.state.memory_wrapper = fake

    client.get("/api/agents/hermes/memory/stats")
    assert fake.calls[0]["dataset"] == "private:hermes"


def test_memory_stats_handles_malformed_payload_gracefully(client: TestClient) -> None:
    """Wrapper returned something that isn't ``{items: [...]}``."""
    fake = _FakeWrapper({"not_items": []})  # type: ignore[arg-type]
    client.app.state.memory_wrapper = fake

    r = client.get("/api/agents/hermes/memory/stats")
    body = r.json()
    # Wrapper IS available (call succeeded) but writes is 0 because the
    # payload was malformed.
    assert body["available"] is True
    assert body["writes"] == 0
    assert body["last_write"] is None
