# test_npu_occupancy.py

> 27 nodes · cohesion 0.15

## Key Concepts

- **test_npu_occupancy.py** (14 connections) — `tests/api/test_npu_occupancy.py`
- **_FakeSlot** (11 connections) — `tests/api/test_npu_occupancy.py`
- **TestClient** (11 connections)
- **_wire()** (11 connections) — `tests/api/test_npu_occupancy.py`
- **_wire_ready_anchor()** (9 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_offline_slot_no_columns()** (5 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_serving_is_active_without_stamp()** (5 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_active_from_recent_last_used()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_contract_field_shape()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_degraded_when_probe_fails()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_inactive_when_last_used_stale()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_loaded_columns_available()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_subslot_activity_independent()** (4 connections) — `tests/api/test_npu_occupancy.py`
- **.__init__()** (3 connections) — `tests/api/test_npu_occupancy.py`
- **test_occupancy_absent_no_npu_no_slots()** (3 connections) — `tests/api/test_npu_occupancy.py`
- **Any** (2 connections)
- **_stub_footprint()** (2 connections) — `tests/api/test_npu_occupancy.py`
- **Tests for ``GET /api/npu/occupancy`` — the NPU occupancy card backend.  Cases:** (1 connections) — `tests/api/test_npu_occupancy.py`
- **An offline flm slot is reported but owns no columns; cols_used=0.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **READY anchor slot with a successful column probe.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **READY (not serving) slot hit within the window → active:true.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **A stamp older than the activity window no longer reads as active.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **SERVING (request in flight) → active even with no last-used stamp.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **flm-stt / flm-embed carry their own activity, not the anchor's.** (1 connections) — `tests/api/test_npu_occupancy.py`
- **Minimal Slot stand-in: name/state/model_id/backend/metadata.** (1 connections) — `tests/api/test_npu_occupancy.py`
- *... and 2 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (2 shared connections)

## Source Files

- `tests/api/test_npu_occupancy.py`

## Audit Trail

- EXTRACTED: 109 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*