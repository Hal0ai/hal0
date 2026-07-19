# test_backends_npu_canonical_fields.py

> 23 nodes

## Key Concepts

- **test_backends_npu_canonical_fields.py** (10 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **TestClient** (7 connections)
- **_seed_slot_toml()** (6 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **_FakeSlot** (6 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_npu_load_idempotent_returns_existing_slot()** (5 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_npu_load_creates_slot_with_all_canonical_fields()** (4 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_install_slot_model_updates_model_default()** (4 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_install_slot_model_preserves_all_existing_top_level_fields()** (4 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_install_slot_model_missing_body_field_returns_400()** (4 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_seeded_npu_toml_fields_survive_model_update()** (4 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **test_install_slot_model_returns_typed_404_for_missing_slot()** (3 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **MonkeyPatch** (2 connections)
- **Path** (1 connections)
- **.__init__()** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **Tests for #770 — canonical slot creation and model-update paths.  Covers:   (a)** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **Minimal Slot stand-in for SlotManager mock returns.** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **npu/load must write device, type, runtime, profile to the slot TOML.      This t** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **A second call with the same model_id must reuse the existing slot     and NOT ca** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **PUT /api/install/slots/npu/model rewrites model.default in the TOML.      The ex** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **All top-level fields in the existing TOML survive a model update.** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **A request for a slot whose TOML does not exist returns a typed 404.      This te** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **Missing model_id in body returns 400, not 500.** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`
- **The seeded npu.toml shape (all canonical fields) is fully preserved     after a** (1 connections) — `tests/api/test_backends_npu_canonical_fields.py`

## Relationships

- [SlotState](SlotState.md) (1 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `tests/api/test_backends_npu_canonical_fields.py`

## Audit Trail

- EXTRACTED: 68 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*