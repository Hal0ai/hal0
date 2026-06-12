"""Tests for ``GET /api/metrics/prometheus`` (Phase E rewrite, #687).

The route renders :func:`hal0.slots.metrics.render_slot_metrics` over the
SlotManager's snapshots — no polling shim, no external daemon. Surface
contract under test:

  * The route returns ``text/plain; version=0.0.4`` per Prometheus spec.
  * Missing SlotManager (lifespan bypassed) → 200 with an empty body —
    scrapers treat that as "no series".
  * Slot snapshots render as ``hal0_slot_up`` (1 for the dispatchable
    ready-set READY/SERVING/IDLE, else 0), one-hot ``hal0_slot_state``,
    and ``hal0_slots_ready_total``, with HELP/TYPE headers and a
    trailing newline.
  * An empty slot list still emits the headers + a zero total.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hal0.slots.state import SlotState


class _FakeSlot:
    """Minimal slot snapshot — render_slot_metrics only reads name + state."""

    def __init__(self, name: str, state: Any) -> None:
        self.name = name
        self.state = state


class _FakeSlotManager:
    def __init__(self, slots: list[Any]) -> None:
        self._slots = slots

    async def list(self) -> list[Any]:
        return self._slots


class _RaisingSlotManager:
    async def list(self) -> list[Any]:
        raise RuntimeError("state dir unreadable")


def test_route_returns_prometheus_content_type(client: TestClient) -> None:
    """text/plain with the version qualifier is the Prometheus contract.

    Scrapers look for ``version=0.0.4`` to pick the parser; without it
    some older collectors fall back to a chunked-binary format and
    silently drop the body.
    """
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    content_type = resp.headers["content-type"]
    assert content_type.startswith("text/plain"), content_type
    assert "version=0.0.4" in content_type


def test_route_with_no_slot_manager_returns_empty_body(client: TestClient) -> None:
    """No SlotManager on app.state (lifespan bypassed / boot failure) →
    200 with empty body. Empty Prometheus exposition = "no series",
    which is the correct "no data yet" signal."""
    client.app.state.slot_manager = None
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    assert resp.text == ""


def test_route_renders_slot_state_exposition(client: TestClient) -> None:
    """Synthetic slot snapshots round-trip through the route exactly.

    Covers both plain-string and SlotState-enum ``state`` values — the
    renderer normalises via ``getattr(state, "value", state)``.
    """
    client.app.state.slot_manager = _FakeSlotManager(
        [
            _FakeSlot("chat", SlotState.READY),
            _FakeSlot("embed", "serving"),
            _FakeSlot("npu", "idle"),
            _FakeSlot("img", SlotState.OFFLINE),
            _FakeSlot("stt", "error"),
        ]
    )
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    body = resp.text

    # HELP/TYPE headers for every series family.
    assert "# HELP hal0_slot_up" in body
    assert "# TYPE hal0_slot_up gauge" in body
    assert "# HELP hal0_slot_state" in body
    assert "# TYPE hal0_slot_state gauge" in body
    assert "# HELP hal0_slots_ready_total" in body
    assert "# TYPE hal0_slots_ready_total gauge" in body

    # hal0_slot_up: 1 for the dispatchable ready-set, 0 otherwise.
    assert 'hal0_slot_up{slot="chat"} 1' in body
    assert 'hal0_slot_up{slot="embed"} 1' in body
    assert 'hal0_slot_up{slot="npu"} 1' in body
    assert 'hal0_slot_up{slot="img"} 0' in body
    assert 'hal0_slot_up{slot="stt"} 0' in body

    # One-hot state indicators.
    assert 'hal0_slot_state{slot="chat",state="ready"} 1' in body
    assert 'hal0_slot_state{slot="embed",state="serving"} 1' in body
    assert 'hal0_slot_state{slot="npu",state="idle"} 1' in body
    assert 'hal0_slot_state{slot="img",state="offline"} 1' in body
    assert 'hal0_slot_state{slot="stt",state="error"} 1' in body

    # Ready total counts READY + SERVING + IDLE only.
    assert "hal0_slots_ready_total 3" in body

    # Exposition is always newline-terminated.
    assert body.endswith("\n")


def test_route_with_empty_slot_list_emits_headers_and_zero_total(
    client: TestClient,
) -> None:
    """No slots is "up and empty", not "no data" — headers + zero total."""
    client.app.state.slot_manager = _FakeSlotManager([])
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    body = resp.text
    assert "# HELP hal0_slot_up" in body
    assert "# TYPE hal0_slot_up gauge" in body
    assert "# HELP hal0_slot_state" in body
    assert "# HELP hal0_slots_ready_total" in body
    assert "hal0_slots_ready_total 0" in body
    assert body.endswith("\n")
    # And no per-slot series.
    assert "hal0_slot_up{" not in body
    assert "hal0_slot_state{" not in body


def test_route_degrades_to_empty_exposition_when_list_fails(client: TestClient) -> None:
    """A SlotManager.list() failure renders the empty exposition rather
    than 500-ing the scrape."""
    client.app.state.slot_manager = _RaisingSlotManager()
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    assert "hal0_slots_ready_total 0" in resp.text


def test_route_is_public(client: TestClient) -> None:
    """Like /api/status + /api/metrics, the Prometheus surface is public.

    Auth-gating would block standard Prometheus scrapers that don't
    speak hal0's agent-identity headers. Operators harden via a reverse
    proxy if they want to limit scraper access. Verified by hitting
    the route without any Authorization header.
    """
    resp = client.get("/api/metrics/prometheus")
    # 200 even without credentials — public route.
    assert resp.status_code == 200
