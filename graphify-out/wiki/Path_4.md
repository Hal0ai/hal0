# Path

> 47 nodes

## Key Concepts

- **Path** (21 connections)
- **pull_job_file()** (15 connections) — `src/hal0/registry/pull.py`
- **_tmp_dir()** (13 connections) — `src/hal0/registry/pull.py`
- **_pull_root()** (11 connections) — `src/hal0/registry/pull.py`
- **_final_path_for_entry()** (10 connections) — `src/hal0/registry/pull.py`
- **test_pull_store.py** (10 connections) — `tests/registry/test_pull_store.py`
- **_sanitise_id()** (8 connections) — `src/hal0/registry/pull.py`
- **_pull_jobs_dir()** (8 connections) — `src/hal0/registry/pull.py`
- **sweep_orphaned_partials()** (7 connections) — `src/hal0/registry/pull.py`
- **_final_path()** (6 connections) — `src/hal0/registry/pull.py`
- **sweep_pull_jobs()** (6 connections) — `src/hal0/registry/pull.py`
- **_staging_paths()** (6 connections) — `src/hal0/registry/pull.py`
- **test_persist_pull_job_fsyncs_parent_dir()** (6 connections) — `tests/registry/test_pull_store.py`
- **test_persist_pull_job_survives_dir_fsync_failure()** (6 connections) — `tests/registry/test_pull_store.py`
- **test_sweep_orphaned_partials_reaps_old_but_keeps_fresh_and_final()** (5 connections) — `tests/registry/test_pull.py`
- **_write_snapshot()** (5 connections) — `tests/registry/test_pull_store.py`
- **_comfyui_models_dir()** (4 connections) — `src/hal0/registry/pull.py`
- **test_sweep_orphaned_partials_missing_tmp_dir_is_noop()** (4 connections) — `tests/registry/test_pull.py`
- **test_sweep_orphaned_partials_reaps_stale_resume_sidecars()** (4 connections) — `tests/registry/test_pull.py`
- **test_sweep_pull_jobs_reaps_only_old_terminal()** (4 connections) — `tests/registry/test_pull_store.py`
- **test_sweep_pull_jobs_missing_dir_returns_zero()** (4 connections) — `tests/registry/test_pull_store.py`
- **test_sanitise_id_blocks_path_traversal()** (3 connections) — `tests/registry/test_pull.py`
- **test_effective_store_picks_pull_root_fallback()** (3 connections) — `tests/registry/test_pull_store.py`
- **test_effective_store_prefers_explicit_store()** (2 connections) — `tests/registry/test_pull_store.py`
- **MonkeyPatch** (2 connections)
- *... and 22 more nodes in this community*

## Relationships

- [pull.py](pull.py.md) (27 shared connections)
- [run_pull](run_pull.md) (17 shared connections)
- [load_hal0_config](load_hal0_config.md) (5 shared connections)
- [connect](connect.md) (2 shared connections)
- [test_curated_image_models.py](test_curated_image_models.py.md) (2 shared connections)
- [lifespan](lifespan.md) (2 shared connections)
- [test_models_crud.py](test_models_crud.py.md) (2 shared connections)
- [ModelsConfig](ModelsConfig.md) (2 shared connections)
- [pull_jobs.py](pull_jobs.py.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/pull.py`
- `tests/registry/test_pull.py`
- `tests/registry/test_pull_store.py`

## Audit Trail

- EXTRACTED: 142 (73%)
- INFERRED: 53 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*