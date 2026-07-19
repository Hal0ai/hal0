# run_pull

> 77 nodes

## Key Concepts

- **run_pull()** (47 connections) — `src/hal0/registry/pull.py`
- **make_job()** (41 connections) — `src/hal0/registry/pull.py`
- **test_pull.py** (40 connections) — `tests/registry/test_pull.py`
- **_payload()** (23 connections) — `tests/registry/test_pull.py`
- **MockTransport** (16 connections)
- **_part_paths()** (13 connections) — `tests/registry/test_pull.py`
- **test_run_pull_cancellation_removes_partial()** (9 connections) — `tests/registry/test_pull.py`
- **Path** (9 connections)
- **test_run_pull_resumes_from_partial_with_range()** (9 connections) — `tests/registry/test_pull.py`
- **test_run_pull_restarts_when_server_ignores_range()** (9 connections) — `tests/registry/test_pull.py`
- **test_run_pull_restarts_when_object_changed()** (9 connections) — `tests/registry/test_pull.py`
- **hf_download_url()** (8 connections) — `src/hal0/registry/pull.py`
- **_ok_handler()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_fails_fast_when_content_length_exceeds_free_disk()** (8 connections) — `tests/registry/test_pull.py`
- **_seed_partial()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_permanent_error_removes_partial()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_cancel_mid_second_file_then_repull_completes()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_transient_error_mid_mmproj_resumes_second_file()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_checksum_mismatch_fails_and_keeps_part()** (8 connections) — `tests/registry/test_pull.py`
- **test_run_pull_persists_terminal_snapshot_for_direct_callers()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_proceeds_when_disk_probe_unavailable()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_task_cancel_preserves_partial_for_resume()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_transient_error_preserves_partial()** (7 connections) — `tests/registry/test_pull.py`
- **test_run_pull_ignores_legacy_id_keyed_partial()** (7 connections) — `tests/registry/test_pull.py`
- **_two_file_transport()** (7 connections) — `tests/registry/test_pull.py`
- *... and 52 more nodes in this community*

## Relationships

- [pull.py](pull.py.md) (17 shared connections)
- [Path](Path.md) (17 shared connections)
- [test_curated_image_models.py](test_curated_image_models.py.md) (4 shared connections)
- [models.py](models.py.md) (2 shared connections)
- [orchestrate.py](orchestrate.py.md) (2 shared connections)
- [pull_jobs.py](pull_jobs.py.md) (2 shared connections)
- [test_pull_shutdown.py](test_pull_shutdown.py.md) (2 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (2 shared connections)
- [test_update_check.py](test_update_check.py.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [types.py](types.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/pull.py`
- `tests/registry/test_pull.py`

## Audit Trail

- EXTRACTED: 314 (69%)
- INFERRED: 138 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*