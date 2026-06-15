"""Tests for ``GET /api/npu/occupancy`` — the NPU occupancy card backend.

Cases:
  (a) no NPU hardware + no flm slots → ``present:false`` minimal payload.
  (b) flm slot loaded + xrt-smi success → cols 0..7, columns_available=true,
      cols_used=8, serving=true.
  (c) flm slot loaded + xrt-smi failure (probe None) → columns_available=false,
      degraded cols all-8.
  (d) response field-shape matches the frontend contract exactly.

The route reads ``app.state.slot_manager`` and locally imports
``hardware._npu_status`` + ``npu_columns.cached_aie_columns`` — so we patch
those at their definition sites and swap the manager's ``list`` for an
AsyncMock returning fake Slot snapshots.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod
import hal0.providers.npu_columns as npu_columns
from hal0.slots.state import SlotState


class _FakeSlot:
    """Minimal Slot stand-in: name/state/model_id/backend/metadata."""

    def __init__(
        self,
        name: str,
        state: SlotState,
        model_id: str | None = None,
        backend: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.name = name
        self.state = state
        self.model_id = model_id
        self.backend = backend
        self.metadata = metadata or {}


def _wire(client: TestClient, slots: list[_FakeSlot]) -> None:
    """Make the app's slot_manager.list() return *slots*."""
    sm = client.app.state.slot_manager
    sm.list = AsyncMock(return_value=slots)


@pytest.fixture(autouse=True)
def _stub_footprint(monkeypatch):
    """Avoid touching the real FLM catalog probe — return a fixed footprint."""
    monkeypatch.setattr(
        "hal0.providers.flm.flm_served_models",
        lambda: [{"tag": "gemma3:4b", "footprint_gb": 2.4, "size_bytes": 0}],
    )
    monkeypatch.setattr("hal0.providers.flm.flm_id_to_tag", lambda _mid: None)
    npu_columns.invalidate_columns_cache()
    yield
    npu_columns.invalidate_columns_cache()


# ── (a) absent ───────────────────────────────────────────────────────────────


def test_occupancy_absent_no_npu_no_slots(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value=None))
    _wire(client, [])

    r = client.get("/api/npu/occupancy")
    assert r.status_code == 200
    body = r.json()
    assert body["present"] is False
    assert body["cols_used"] == 0
    assert body["serving"] is False
    assert body["columns_available"] is False
    assert body["single_tenant"] is True
    assert body["slots"] == []
    # geometry constants still present
    assert body["rows"] == 4
    assert body["cols"] == 8
    assert body["tiles"] == 32
    assert body["tops_peak"] == 50
    assert body["cols_total"] == 8


# ── (b) loaded + xrt-smi success ─────────────────────────────────────────────


def test_occupancy_loaded_columns_available(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(
        npu_columns,
        "cached_aie_columns",
        AsyncMock(
            return_value={
                "partitions": [{"start_col": 0, "num_cols": 8, "contexts": 1}],
                "total": 8,
            }
        ),
    )
    _wire(
        client,
        [
            _FakeSlot(
                "npu",
                SlotState.SERVING,
                model_id="gemma3:4b",
                metadata={"provider": "flm"},
            )
        ],
    )

    body = client.get("/api/npu/occupancy").json()
    assert body["present"] is True
    assert body["columns_available"] is True
    assert body["cols_used"] == 8
    assert body["serving"] is True
    assert len(body["slots"]) == 1
    slot = body["slots"][0]
    assert slot["name"] == "npu"
    assert slot["model"] == "gemma3:4b"
    assert slot["state"] == "serving"
    assert slot["cols"] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert slot["gb"] == 2.4


# ── (c) loaded + xrt-smi failure → degraded ─────────────────────────────────


def test_occupancy_degraded_when_probe_fails(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot(
                "npu",
                SlotState.READY,
                model_id="gemma3:4b",
                backend="flm",
            )
        ],
    )

    body = client.get("/api/npu/occupancy").json()
    assert body["present"] is True
    assert body["columns_available"] is False
    assert body["cols_used"] == 8
    slot = body["slots"][0]
    assert slot["state"] == "ready"
    # degraded single-tenant fallback: owns all 8 columns
    assert slot["cols"] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_occupancy_offline_slot_no_columns(client: TestClient, monkeypatch):
    """An offline flm slot is reported but owns no columns; cols_used=0."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot(
                "npu",
                SlotState.OFFLINE,
                model_id="gemma3:4b",
                backend="flm",
            )
        ],
    )

    body = client.get("/api/npu/occupancy").json()
    assert body["columns_available"] is False
    assert body["cols_used"] == 0
    assert body["serving"] is False
    slot = body["slots"][0]
    assert slot["state"] == "offline"
    assert slot["cols"] == []


# ── (d) field-shape contract ────────────────────────────────────────────────


def test_occupancy_contract_field_shape(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(
        npu_columns,
        "cached_aie_columns",
        AsyncMock(
            return_value={
                "partitions": [{"start_col": 0, "num_cols": 8, "contexts": 1}],
                "total": 8,
            }
        ),
    )
    _wire(
        client,
        [_FakeSlot("npu", SlotState.SERVING, model_id="gemma3:4b", metadata={"provider": "flm"})],
    )

    body = client.get("/api/npu/occupancy").json()
    top_keys = {
        "present",
        "rows",
        "cols",
        "tiles",
        "tops_peak",
        "cols_total",
        "cols_used",
        "serving",
        "single_tenant",
        "columns_available",
        "slots",
    }
    assert set(body.keys()) == top_keys
    assert set(body["slots"][0].keys()) == {"name", "model", "state", "cols", "gb"}
