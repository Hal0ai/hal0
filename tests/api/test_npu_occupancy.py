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

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod
import hal0.api.routes.npu as _npu_routes
import hal0.providers.npu_columns as npu_columns
from hal0.slots.state import SlotState

# Captured at import, before the autouse fixture stubs it, for the cache tests.
_REAL_LOCAL_IMAGE_REFS = _npu_routes._local_image_refs


class _FakeSlot:
    """Minimal Slot stand-in: name/state/model_id/backend/metadata."""

    def __init__(
        self,
        name: str,
        state: SlotState,
        model_id: str | None = None,
        backend: str | None = None,
        metadata: dict[str, Any] | None = None,
        npu: dict[str, Any] | None = None,
    ):
        self.name = name
        self.state = state
        self.model_id = model_id
        self.backend = backend
        self.metadata = metadata or {}
        if npu is not None:
            self.npu = npu


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
    # Hermetic engine/host facts: never shell out to lsmod / podman.
    monkeypatch.setattr(
        "hal0.api.routes.npu._npu_host_probe",
        lambda: {"device_node": None, "driver_loaded": None, "xdna_gen": None},
    )
    monkeypatch.setattr("hal0.api.routes.npu._local_image_refs", lambda: None)
    # Facts block is cached 30 s with a background refresh: start every test with a
    # fresh all-None entry so no stray refresh thread runs; tests that need real
    # facts call _prime_facts() after stubbing.
    monkeypatch.setattr(_npu_routes, "_facts_cache", (time.monotonic(), _npu_routes._none_facts()))
    monkeypatch.setattr(_npu_routes, "_facts_refresh_thread", None)
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
        "shared_warning",
        "npu_engines",
        "npu_host",
    }
    assert set(body.keys()) == top_keys
    assert set(body["slots"][0].keys()) == {
        "name",
        "model",
        "state",
        "engine",
        "cols",
        "gb",
        "active",
        "last_used_age_s",
    }
    assert body["slots"][0]["engine"] == "flm"


# ── (e) per-capability activity ──────────────────────────────────────────────


def _wire_ready_anchor(client: TestClient, monkeypatch, npu: dict[str, Any] | None = None):
    """READY anchor slot with a successful column probe."""
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
                SlotState.READY,
                model_id="gemma3:4b",
                metadata={"provider": "flm"},
                npu=npu,
            )
        ],
    )


def test_occupancy_active_from_recent_last_used(client: TestClient, monkeypatch):
    """READY (not serving) slot hit within the window → active:true."""
    _wire_ready_anchor(client, monkeypatch)
    client.app.state.slot_last_used = {"npu": time.monotonic() - 1.0}

    slot = client.get("/api/npu/occupancy").json()["slots"][0]
    assert slot["active"] is True
    assert 0.5 <= slot["last_used_age_s"] <= 2.0


def test_occupancy_inactive_when_last_used_stale(client: TestClient, monkeypatch):
    """A stamp older than the activity window no longer reads as active."""
    _wire_ready_anchor(client, monkeypatch)
    client.app.state.slot_last_used = {"npu": time.monotonic() - 60.0}

    slot = client.get("/api/npu/occupancy").json()["slots"][0]
    assert slot["active"] is False
    assert slot["last_used_age_s"] >= 59.0


def test_occupancy_serving_is_active_without_stamp(client: TestClient, monkeypatch):
    """SERVING (request in flight) → active even with no last-used stamp."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [_FakeSlot("npu", SlotState.SERVING, model_id="gemma3:4b", backend="flm")],
    )
    client.app.state.slot_last_used = {}

    slot = client.get("/api/npu/occupancy").json()["slots"][0]
    assert slot["active"] is True
    assert slot["last_used_age_s"] is None


def test_occupancy_subslot_activity_independent(client: TestClient, monkeypatch):
    """flm-stt / flm-embed carry their own activity, not the anchor's."""
    _wire_ready_anchor(client, monkeypatch, npu={"asr": True, "embed": True})
    client.app.state.slot_last_used = {"npu-stt": time.monotonic() - 1.0}

    by_name = {s["name"]: s for s in client.get("/api/npu/occupancy").json()["slots"]}
    assert by_name["npu"]["active"] is False
    assert by_name["npu-stt"]["active"] is True
    assert by_name["npu-embed"]["active"] is False


# ── xdna/flm coexistence (xdna-runner plan §4) ────────────────────────────────


def test_occupancy_flm_companions_are_not_a_second_tenant(client: TestClient, monkeypatch):
    """The flm anchor plus its live -stt/-embed companion rows are one FLM
    process, not several tenants: no shared_warning, single_tenant stays true."""
    _wire_ready_anchor(client, monkeypatch, npu={"asr": True, "embed": True})

    body = client.get("/api/npu/occupancy").json()
    names = {s["name"] for s in body["slots"]}
    assert {"npu", "npu-stt", "npu-embed"} <= names
    assert body["shared_warning"] is None
    assert body["single_tenant"] is True


def test_occupancy_single_npu_slot_has_no_shared_warning(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [_FakeSlot("npu", SlotState.SERVING, model_id="gemma3:4b", backend="flm")],
    )

    body = client.get("/api/npu/occupancy").json()
    assert body["shared_warning"] is None


def test_occupancy_warns_when_xdna_and_flm_both_loaded(client: TestClient, monkeypatch):
    """An xdna (backend=npu) slot and the flm anchor both loaded → shared_warning."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot("flm", SlotState.READY, model_id="gemma3:4b", backend="flm"),
            _FakeSlot("xdna-qwen", SlotState.READY, model_id="qwen3-xdna", backend="npu"),
        ],
    )

    body = client.get("/api/npu/occupancy").json()
    names = {s["name"] for s in body["slots"]}
    assert {"flm", "xdna-qwen"} <= names
    assert body["shared_warning"] is not None
    assert "time-sliced" in body["shared_warning"]


def test_occupancy_engine_field_and_single_tenant(client: TestClient, monkeypatch):
    """``engine`` distinguishes flm vs xdna holders; single_tenant false when shared."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot("flm", SlotState.READY, model_id="gemma3:4b", backend="flm"),
            _FakeSlot("xdna-qwen", SlotState.READY, model_id="qwen3-xdna", backend="npu"),
        ],
    )

    body = client.get("/api/npu/occupancy").json()
    by_name = {s["name"]: s for s in body["slots"]}
    assert by_name["flm"]["engine"] == "flm"
    assert by_name["xdna-qwen"]["engine"] == "xdna"
    assert body["single_tenant"] is False


def test_occupancy_single_tenant_true_for_one_holder(client: TestClient, monkeypatch):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [_FakeSlot("xdna-qwen", SlotState.READY, model_id="qwen3-xdna", backend="npu")],
    )

    body = client.get("/api/npu/occupancy").json()
    assert body["single_tenant"] is True
    assert body["slots"][0]["engine"] == "xdna"


def test_occupancy_engine_reads_the_xdna_binary_from_config(client: TestClient, monkeypatch):
    """A stale ``flm`` backend mirror on an xdna slot still reports ``engine: xdna``."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot("flm", SlotState.READY, model_id="gemma3:4b", backend="flm"),
            _FakeSlot("xdna-qwen", SlotState.READY, model_id="qwen3-xdna", backend="flm"),
        ],
    )
    sm = client.app.state.slot_manager
    sm.iter_configs = AsyncMock(
        return_value=[
            {"name": "flm", "device": "npu", "type": "llm"},
            {"name": "xdna-qwen", "device": "npu", "type": "llm", "binary": "xdna"},
        ]
    )

    body = client.get("/api/npu/occupancy").json()
    by_name = {s["name"]: s for s in body["slots"]}
    assert by_name["flm"]["engine"] == "flm"
    assert by_name["xdna-qwen"]["engine"] == "xdna"


def test_occupancy_keeps_xdna_slot_named_with_companion_suffix(client: TestClient, monkeypatch):
    """An xdna slot named ``*-embed`` / ``*-stt`` is a real NPU holder, not a FLM companion."""
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    _wire(
        client,
        [
            _FakeSlot("flm", SlotState.READY, model_id="gemma3:4b", backend="flm"),
            _FakeSlot("test-embed", SlotState.READY, model_id="e", backend="npu"),
            _FakeSlot("notes-stt", SlotState.READY, model_id="s", backend="npu"),
        ],
    )
    sm = client.app.state.slot_manager
    sm.iter_configs = AsyncMock(
        return_value=[
            {"name": "test-embed", "device": "npu", "type": "llm", "binary": "xdna"},
            {"name": "notes-stt", "device": "npu", "type": "llm", "binary": "xdna"},
        ]
    )

    body = client.get("/api/npu/occupancy").json()
    by_name = {s["name"]: s for s in body["slots"]}
    assert by_name["test-embed"]["engine"] == "xdna"
    assert by_name["notes-stt"]["engine"] == "xdna"
    assert body["single_tenant"] is False
    assert body["shared_warning"] is not None


# ── engine + host facts ──────────────────────────────────────────────────────

_XDNA_REF = "ghcr.io/hal0ai/hal0-runner-xdna:1.2.3-r1a2b3c4"
_FLM_REF = "ghcr.io/hal0ai/hal0-runner-flm:0.9.44-r1a2b3c4"


def _prime_facts():
    """Compute the facts block synchronously into the cache (the route never blocks on it)."""
    _npu_routes._refresh_facts()


def _stub_engine_facts(monkeypatch, *, refs, host):
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._effective_defaults",
        lambda: {"xdna": (_XDNA_REF, "release"), "flm": (_FLM_REF, "release")},
    )
    monkeypatch.setattr("hal0.api.routes.npu._local_image_refs", lambda: refs)
    monkeypatch.setattr("hal0.api.routes.npu._npu_host_probe", lambda: host)
    _prime_facts()


def test_occupancy_reports_npu_engines_and_host(client: TestClient, monkeypatch):
    _stub_engine_facts(
        monkeypatch,
        refs={_XDNA_REF},
        host={"device_node": "/dev/accel/accel0", "driver_loaded": True, "xdna_gen": 2},
    )
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(
        npu_columns,
        "cached_aie_columns",
        AsyncMock(return_value={"partitions": [{"start_col": 0, "num_cols": 8}], "total": 8}),
    )
    _wire(
        client,
        [_FakeSlot("npu", SlotState.READY, model_id="gemma3:4b", metadata={"provider": "flm"})],
    )
    body = client.get("/api/npu/occupancy").json()
    assert body["npu_engines"] == {
        "flm": {"image": _FLM_REF, "version": "0.9.44"},
        "xdna": {"image": _XDNA_REF, "tag": "1.2.3-r1a2b3c4", "pulled": True},
    }
    assert "hal0-runner-xdna" in body["npu_engines"]["xdna"]["image"]
    assert body["npu_host"] == {
        "device_node": "/dev/accel/accel0",
        "driver_loaded": True,
        "xdna_gen": 2,
    }


def test_absent_occupancy_still_reports_engines(client: TestClient, monkeypatch):
    _stub_engine_facts(
        monkeypatch,
        refs=set(),
        host={"device_node": None, "driver_loaded": False, "xdna_gen": None},
    )
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value=None))
    _wire(client, [])
    body = client.get("/api/npu/occupancy").json()
    assert body["present"] is False
    assert body["npu_engines"]["xdna"]["pulled"] is False
    assert "flm" in body["npu_engines"]["flm"]["image"]
    assert body["npu_host"] == {"device_node": None, "driver_loaded": False, "xdna_gen": None}


def test_engine_facts_are_none_when_every_read_fails(client: TestClient, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr("hal0.api.routes.runner_images._effective_defaults", boom)
    monkeypatch.setattr("hal0.api.routes.npu._local_image_refs", boom)
    monkeypatch.setattr("hal0.api.routes.npu._npu_host_probe", boom)
    _prime_facts()
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value=None))
    _wire(client, [])
    r = client.get("/api/npu/occupancy")
    assert r.status_code == 200
    body = r.json()
    assert body["npu_engines"] == {
        "flm": {"image": None, "version": None},
        "xdna": {"image": None, "tag": None, "pulled": None},
    }
    assert body["npu_host"] == {"device_node": None, "driver_loaded": None, "xdna_gen": None}


# ── image-store cache: stale-while-revalidate, single-flight ─────────────────


@pytest.fixture
def image_store(monkeypatch):
    import hal0.api.routes.npu as npu_mod

    monkeypatch.setattr(npu_mod, "_local_image_refs", _REAL_LOCAL_IMAGE_REFS)
    monkeypatch.setattr(npu_mod, "_image_store_cache", None)
    monkeypatch.setattr(npu_mod, "_image_refresh_thread", None)
    return npu_mod


def _join(npu_mod):
    t = npu_mod._image_refresh_thread
    if t is not None:
        t.join(timeout=5)


def test_cold_cache_returns_none_and_starts_one_refresh(image_store, monkeypatch):
    import threading

    npu_mod = image_store
    gate = threading.Event()
    calls: list[int] = []

    def slow_read():
        calls.append(1)
        gate.wait(5)
        return {"a"}

    monkeypatch.setattr(npu_mod, "_read_image_store", slow_read)
    assert [npu_mod._local_image_refs() for _ in range(5)] == [None] * 5
    gate.set()
    _join(npu_mod)
    assert len(calls) == 1
    assert npu_mod._local_image_refs() == {"a"}
    assert len(calls) == 1  # fresh: no new refresh


def test_expiry_serves_stale_and_triggers_one_new_refresh(image_store, monkeypatch):
    npu_mod = image_store
    values = iter([{"old"}, {"new"}])
    monkeypatch.setattr(npu_mod, "_read_image_store", lambda: next(values))
    npu_mod._local_image_refs()
    _join(npu_mod)
    assert npu_mod._local_image_refs() == {"old"}
    ts, val = npu_mod._image_store_cache
    monkeypatch.setattr(npu_mod, "_image_store_cache", (ts - 1000, val))
    assert npu_mod._local_image_refs() == {"old"}  # stale served immediately
    _join(npu_mod)
    assert npu_mod._local_image_refs() == {"new"}


def test_failed_refresh_is_cached_as_none(image_store, monkeypatch):
    npu_mod = image_store
    calls: list[int] = []

    def fail():
        calls.append(1)
        return None

    monkeypatch.setattr(npu_mod, "_read_image_store", fail)
    npu_mod._local_image_refs()
    _join(npu_mod)
    assert npu_mod._local_image_refs() is None
    assert len(calls) == 1  # failure cached for the TTL, no retry stampede


def test_image_pulled_matches_ref_base_and_digest():
    from hal0.api.routes.npu import _image_pulled

    assert _image_pulled("r/x:1", None) is None
    assert _image_pulled("r/x:1", {"r/x:1"}) is True
    assert _image_pulled("r/x:1@sha256:abc", {"r/x:1"}) is True  # base ref
    assert _image_pulled("r/x:1@sha256:abc", {"sha256:abc"}) is True  # digest
    assert _image_pulled("r/x:2", {"r/x:1"}) is False


# ── served_by tag on synthesized rows ────────────────────────────────────────


def test_occupancy_tags_synthesized_rows_with_their_anchor(client: TestClient, monkeypatch):
    """The anchor's -stt/-embed rows say who serves them; the anchor row has no tag."""
    _wire_ready_anchor(client, monkeypatch, npu={"asr": True, "embed": True})

    by_name = {s["name"]: s for s in client.get("/api/npu/occupancy").json()["slots"]}
    assert by_name["npu-stt"]["served_by"] == "npu"
    assert by_name["npu-embed"]["served_by"] == "npu"
    assert "served_by" not in by_name["npu"]


# ── facts block cache (30 s, stale-while-revalidate) ─────────────────────────


@pytest.fixture
def facts_cache(monkeypatch):
    monkeypatch.setattr(_npu_routes, "_facts_cache", None)
    monkeypatch.setattr(_npu_routes, "_facts_refresh_thread", None)
    return _npu_routes


def _join_facts(npu_mod):
    t = npu_mod._facts_refresh_thread
    if t is not None:
        t.join(timeout=5)


def test_facts_first_call_is_all_none_then_cached(facts_cache, monkeypatch):
    import asyncio

    npu_mod = facts_cache
    calls: list[int] = []

    def probe():
        calls.append(1)
        return {"device_node": "/dev/accel/accel0", "driver_loaded": True, "xdna_gen": 2}

    monkeypatch.setattr(npu_mod, "_npu_host_probe", probe)
    first = asyncio.run(npu_mod._engine_and_host_facts())
    assert first == npu_mod._none_facts()  # never blocks on the read
    _join_facts(npu_mod)
    second = asyncio.run(npu_mod._engine_and_host_facts())
    assert second["npu_host"]["device_node"] == "/dev/accel/accel0"
    for _ in range(5):
        asyncio.run(npu_mod._engine_and_host_facts())
    assert len(calls) == 1  # TTL hit: no per-poll probe


def test_facts_expiry_serves_last_value_and_refreshes_once(facts_cache, monkeypatch):
    import asyncio

    npu_mod = facts_cache
    nodes = iter(["old", "new"])
    monkeypatch.setattr(
        npu_mod,
        "_npu_host_probe",
        lambda: {"device_node": next(nodes), "driver_loaded": None, "xdna_gen": None},
    )
    asyncio.run(npu_mod._engine_and_host_facts())
    _join_facts(npu_mod)
    ts, val = npu_mod._facts_cache
    monkeypatch.setattr(npu_mod, "_facts_cache", (ts - 1000, val))
    stale = asyncio.run(npu_mod._engine_and_host_facts())
    assert stale["npu_host"]["device_node"] == "old"  # last value, immediately
    _join_facts(npu_mod)
    assert asyncio.run(npu_mod._engine_and_host_facts())["npu_host"]["device_node"] == "new"


# ── cache invalidation when the image store changes (#3645) ──────────────────


def test_invalidate_makes_next_poll_reread_and_keeps_last_value(facts_cache, monkeypatch):
    import asyncio

    npu_mod = facts_cache
    monkeypatch.setattr(npu_mod, "_local_image_refs", _REAL_LOCAL_IMAGE_REFS)
    monkeypatch.setattr(npu_mod, "_image_store_cache", None)
    monkeypatch.setattr(npu_mod, "_image_refresh_thread", None)
    ref = "ghcr.io/x/ggml-xdna:1"
    monkeypatch.setattr(npu_mod, "_npu_host_probe", lambda: {})
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._effective_defaults",
        lambda: {"xdna": (ref, "x"), "flm": ("", "")},
    )
    store: list[set[str] | None] = [set()]
    monkeypatch.setattr(npu_mod, "_read_image_store", lambda: store[0])

    def pulled():
        return asyncio.run(npu_mod._engine_and_host_facts())["npu_engines"]["xdna"]["pulled"]

    def settle():
        for name in ("_image_refresh_thread", "_facts_refresh_thread"):
            with npu_mod._image_refresh_lock:
                t = getattr(npu_mod, name)
            if t is not None:
                t.join(timeout=5)

    npu_mod._local_image_refs()  # land the first store read before the facts read
    settle()
    pulled()
    settle()
    assert pulled() is False

    store[0] = {ref}  # the pull finishes
    assert pulled() is False  # both caches still fresh: the reported bug
    npu_mod.invalidate_engine_caches()
    assert pulled() is False  # stale value served, never flickers to None
    settle()
    pulled()
    settle()
    assert pulled() is True


def test_inflight_refresh_started_before_invalidate_does_not_repopulate(monkeypatch):
    import threading

    from hal0.api.routes import npu as npu_mod

    monkeypatch.setattr(npu_mod, "_local_image_refs", _REAL_LOCAL_IMAGE_REFS)
    monkeypatch.setattr(npu_mod, "_image_store_cache", None)
    monkeypatch.setattr(npu_mod, "_image_refresh_thread", None)
    gate = threading.Event()

    def slow_read():
        gate.wait(5)
        return set()  # pre-pull snapshot

    monkeypatch.setattr(npu_mod, "_read_image_store", slow_read)
    npu_mod._local_image_refs()
    t = npu_mod._image_refresh_thread
    npu_mod.invalidate_engine_caches()  # pull finished mid-read
    gate.set()
    t.join(timeout=5)
    assert npu_mod._image_store_cache is None  # the stale read was discarded


def test_pull_job_terminal_state_invalidates_npu_caches(monkeypatch):
    from hal0.api.routes import npu as npu_mod
    from hal0.registry import runner_pull_jobs

    calls: list[int] = []
    monkeypatch.setattr(npu_mod, "invalidate_engine_caches", lambda: calls.append(1))
    runner_pull_jobs._invalidate_npu_caches()
    assert calls == [1]
