# test_pull_routes.py

> 54 nodes

## Key Concepts

- **test_pull_routes.py** (27 connections) — `tests/api/test_pull_routes.py`
- **TestClient** (23 connections)
- **Any** (19 connections)
- **FastAPI** (15 connections)
- **fake_run_pull()** (6 connections) — `tests/api/test_pull_routes.py`
- **test_pull_curated_mmproj_file_is_wired()** (6 connections) — `tests/api/test_pull_routes.py`
- **test_pull_persists_job_to_disk()** (6 connections) — `tests/api/test_pull_routes.py`
- **test_pull_status_falls_back_to_disk_after_restart()** (6 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_hf_coords_used_when_id_not_registered()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_chat_template_seeds_defaults()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_chat_template_auto_seeds_no_default()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_chat_template_patches_existing_row()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_hf_coords_override_registry_entry()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_idempotent_when_already_running()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_cancel_flips_flag()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_status_reconciles_stale_inflight_to_failed_after_restart()** (5 connections) — `tests/api/test_pull_routes.py`
- **test_pull_status_reports_completed_when_registry_has_installed_model_despite_stale_snapshot()** (5 connections) — `tests/api/test_pull_routes.py`
- **Path** (5 connections)
- **test_pull_status_reports_failed_when_model_not_installed()** (5 connections) — `tests/api/test_pull_routes.py`
- **app_isolated()** (4 connections) — `tests/api/test_pull_routes.py`
- **test_pull_returns_job_handle_and_kicks_background_task()** (4 connections) — `tests/api/test_pull_routes.py`
- **test_pull_unknown_model_returns_invalid_source()** (4 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_partial_hf_coords_falls_back_to_resolver()** (4 connections) — `tests/api/test_pull_routes.py`
- **test_pull_threads_capability_to_run_pull()** (4 connections) — `tests/api/test_pull_routes.py`
- **test_pull_body_capability_overrides()** (4 connections) — `tests/api/test_pull_routes.py`
- *... and 29 more nodes in this community*

## Relationships

- [test_pull_shutdown.py](test_pull_shutdown.py.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)

## Source Files

- `tests/api/test_pull_routes.py`

## Audit Trail

- EXTRACTED: 224 (98%)
- INFERRED: 4 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*