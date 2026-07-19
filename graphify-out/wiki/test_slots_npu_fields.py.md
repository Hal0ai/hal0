# test_slots_npu_fields.py

> 15 nodes · cohesion 0.20

## Key Concepts

- **test_slots_npu_fields.py** (8 connections) — `tests/api/test_slots_npu_fields.py`
- **app_with_npu_slots()** (5 connections) — `tests/api/test_slots_npu_fields.py`
- **test_put_config_npu_roundtrip()** (5 connections) — `tests/api/test_slots_npu_fields.py`
- **TestClient** (4 connections)
- **_seed_slot_toml()** (4 connections) — `tests/api/test_slots_npu_fields.py`
- **client_with_npu_slots()** (3 connections) — `tests/api/test_slots_npu_fields.py`
- **FastAPI** (3 connections)
- **test_slot_list_includes_npu_toggles()** (3 connections) — `tests/api/test_slots_npu_fields.py`
- **test_slot_without_npu_table_omits_field()** (3 connections) — `tests/api/test_slots_npu_fields.py`
- **Path** (1 connections)
- **Tests for [npu] asr/embed toggle fields on /api/slots (A8).  Verifies:   - ``npu** (1 connections) — `tests/api/test_slots_npu_fields.py`
- **Slot without a [npu] section must NOT have a 'npu' key in the response.** (1 connections) — `tests/api/test_slots_npu_fields.py`
- **PUT /api/slots/npu/config {npu: {asr: true}} -> GET shows asr=true.** (1 connections) — `tests/api/test_slots_npu_fields.py`
- **App with one NPU container slot (has [npu] table) and one plain chat slot.** (1 connections) — `tests/api/test_slots_npu_fields.py`
- **Container NPU slot with [npu] table exposes npu={asr, embed} on /api/slots.** (1 connections) — `tests/api/test_slots_npu_fields.py`

## Relationships

- [create_app](create_app.md) (2 shared connections)

## Source Files

- `tests/api/test_slots_npu_fields.py`

## Audit Trail

- EXTRACTED: 42 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*