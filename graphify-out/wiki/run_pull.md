# run_pull

> 79 nodes · cohesion 0.07

## Key Concepts

- **run_pull()** (47 connections) — `src/hal0/registry/pull.py`
- **make_job()** (41 connections) — `src/hal0/registry/pull.py`
- **test_pull.py** (40 connections) — `tests/registry/test_pull.py`
- **_payload()** (23 connections) — `tests/registry/test_pull.py`
- **MockTransport** (16 connections)
- **_part_paths()** (13 connections) — `tests/registry/test_pull.py`
- **Path** (9 connections)
- **test_run_pull_cancellation_removes_partial()** (9 connections) — `tests/registry/test_pull.py`
- **test_run_pull_restarts_when_object_changed()** (9 connections) — `tests/registry/test_pull.py`
- **test_run_pull_restarts_when_server_ignores_range()** (9 connections) — `tests/registry/test_pull.py`
- **test_run_pull_resumes_from_partial_with_range()** (9 connections) — `tests/registry/test_pull.py`
- **hf_download_url()** (8 connections) — `src/hal0/registry/pull.py`
- **_ok_handler()** (8 connections) — `tests/registry/test_pull.py`
- **_seed_partial()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_cancel_mid_second_file_then_repull_completes()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_checksum_mismatch_fails_and_keeps_part()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_fails_fast_when_content_length_exceeds_free_disk()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_permanent_error_removes_partial()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_transient_error_mid_mmproj_resumes_second_file()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_ignores_legacy_id_keyed_partial()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_multi_file_fetches_mmproj_and_sets_registry()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_multi_file_progress_monotonic_across_boundary()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_persists_terminal_snapshot_for_direct_callers()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_proceeds_when_disk_probe_unavailable()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_task_cancel_preserves_partial_for_resume()** (7 connections) — `tests/registry/test_pull.py`
- *... and 54 more nodes in this community*

## Relationships

- [Model](Model.md) (27 shared connections)
- [_pull_root](_pull_root.md) (7 shared connections)
- [test_curated_image_models.py](test_curated_image_models.py.md) (3 shared connections)
- [models.py](models.py.md) (2 shared connections)
- [orchestrate.py](orchestrate.py.md) (2 shared connections)
- [get_curated](get_curated.md) (2 shared connections)
- [test_pull_shutdown.py](test_pull_shutdown.py.md) (2 shared connections)
- [test_store_golden.py](test_store_golden.py.md) (2 shared connections)
- [test_update_check.py](test_update_check.py.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [types.py](types.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/pull.py`
- `tests/registry/test_curated_image_models.py`
- `tests/registry/test_pull.py`

## Audit Trail

- EXTRACTED: 317 (69%)
- INFERRED: 140 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*