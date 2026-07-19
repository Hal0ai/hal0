# test_models_crud.py

> 60 nodes · cohesion 0.08

## Key Concepts

- **test_models_crud.py** (35 connections) — `tests/api/test_models_crud.py`
- **TestClient** (29 connections)
- **Path** (27 connections)
- **_events_since()** (11 connections) — `tests/api/test_models_crud.py`
- **_max_event_id()** (11 connections) — `tests/api/test_models_crud.py`
- **test_delete_cascade_clears_slot_default_and_emits_model_deleted_last()** (8 connections) — `tests/api/test_models_crud.py`
- **test_create_emits_registered_with_source()** (6 connections) — `tests/api/test_models_crud.py`
- **test_delete_unreferenced_model_emits_with_empty_affected_slots()** (6 connections) — `tests/api/test_models_crud.py`
- **test_duplicate_creates_new_row_sharing_weights()** (6 connections) — `tests/api/test_models_crud.py`
- **test_scan_legacy_empty_body_still_auto_registers()** (6 connections) — `tests/api/test_models_crud.py`
- **test_scan_with_rows_persists_user_overrides()** (6 connections) — `tests/api/test_models_crud.py`
- **test_update_changed_fields_only_lists_actual_changes()** (6 connections) — `tests/api/test_models_crud.py`
- **container_stub()** (5 connections) — `tests/api/test_models_crud.py`
- **crud_app()** (5 connections) — `tests/api/test_models_crud.py`
- **FastAPI** (5 connections)
- **test_create_defaults_source_to_manual()** (5 connections) — `tests/api/test_models_crud.py`
- **test_delete_reaps_pull_job_snapshot()** (5 connections) — `tests/api/test_models_crud.py`
- **test_delete_succeeds_when_snapshot_absent()** (5 connections) — `tests/api/test_models_crud.py`
- **test_duplicate_with_profile_stamps_flags_into_defaults()** (5 connections) — `tests/api/test_models_crud.py`
- **test_model_registered_reaches_live_subscriber()** (5 connections) — `tests/api/test_models_crud.py`
- **test_update_accepts_new_editable_fields_and_emits()** (5 connections) — `tests/api/test_models_crud.py`
- **test_delete_force_cascade_false_returns_409_with_affected_slots()** (4 connections) — `tests/api/test_models_crud.py`
- **test_put_bare_double_quoted_json_extra_args_rejected()** (4 connections) — `tests/api/test_models_crud.py`
- **test_put_profile_change_does_not_rematerialize_extra_args()** (4 connections) — `tests/api/test_models_crud.py`
- **test_put_single_quoted_json_extra_args_accepted()** (4 connections) — `tests/api/test_models_crud.py`
- *... and 35 more nodes in this community*

## Relationships

- [Model](Model.md) (2 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)

## Source Files

- `tests/api/test_models_crud.py`

## Audit Trail

- EXTRACTED: 272 (98%)
- INFERRED: 5 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*