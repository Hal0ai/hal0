# _pull_root

> 31 nodes · cohesion 0.08

## Key Concepts

- **_pull_root()** (11 connections) — `src/hal0/registry/pull.py`
- **test_pull_store.py** (10 connections) — `tests/registry/test_pull_store.py`
- **sweep_orphaned_partials()** (7 connections) — `src/hal0/registry/pull.py`
- **sweep_pull_jobs()** (6 connections) — `src/hal0/registry/pull.py`
- **test_persist_pull_job_fsyncs_parent_dir()** (6 connections) — `tests/registry/test_pull_store.py`
- **test_persist_pull_job_survives_dir_fsync_failure()** (6 connections) — `tests/registry/test_pull_store.py`
- **test_pull_root_uses_store_when_set()** (6 connections) — `tests/registry/test_pull_store.py`
- **test_pull_root_defaults_to_pull_root_when_store_unset()** (5 connections) — `tests/registry/test_pull_store.py`
- **_write_snapshot()** (5 connections) — `tests/registry/test_pull_store.py`
- **test_sweep_orphaned_partials_reaps_old_but_keeps_fresh_and_final()** (5 connections) — `tests/registry/test_pull.py`
- **test_sweep_pull_jobs_missing_dir_returns_zero()** (4 connections) — `tests/registry/test_pull_store.py`
- **test_sweep_pull_jobs_reaps_only_old_terminal()** (4 connections) — `tests/registry/test_pull_store.py`
- **test_sweep_orphaned_partials_missing_tmp_dir_is_noop()** (4 connections) — `tests/registry/test_pull.py`
- **test_sweep_orphaned_partials_reaps_stale_resume_sidecars()** (4 connections) — `tests/registry/test_pull.py`
- **Path** (3 connections)
- **test_effective_store_picks_pull_root_fallback()** (3 connections) — `tests/registry/test_pull_store.py`
- **MonkeyPatch** (2 connections)
- **test_effective_store_prefers_explicit_store()** (2 connections) — `tests/registry/test_pull_store.py`
- **Return the configured pull destination root.      ML-3: thin delegator to :func:** (1 connections) — `src/hal0/registry/pull.py`
- **Delete stale ``*.part`` staging files left by SIGKILL/OOM mid-pull.      Best-ef** (1 connections) — `src/hal0/registry/pull.py`
- **Garbage-collect stale terminal pull-job snapshots (#MR-8).      Reap on-disk sna** (1 connections) — `src/hal0/registry/pull.py`
- **Aged *.part files are reaped; fresh partials and installed files survive.** (1 connections) — `tests/registry/test_pull.py`
- **No .tmp directory present → returns 0 and never raises (fail-soft).** (1 connections) — `tests/registry/test_pull.py`
- **A stale .part.json resume sidecar is reaped too, not left to linger.** (1 connections) — `tests/registry/test_pull.py`
- **Test that ``_pull_root`` honours [models].store with pull_root fallback.** (1 connections) — `tests/registry/test_pull_store.py`
- *... and 6 more nodes in this community*

## Relationships

- [Model](Model.md) (18 shared connections)
- [run_pull](run_pull.md) (7 shared connections)
- [load_hal0_config](load_hal0_config.md) (4 shared connections)
- [ModelsConfig](ModelsConfig.md) (3 shared connections)
- [lifespan](lifespan.md) (2 shared connections)

## Source Files

- `src/hal0/registry/pull.py`
- `tests/registry/test_pull.py`
- `tests/registry/test_pull_store.py`

## Audit Trail

- EXTRACTED: 68 (64%)
- INFERRED: 38 (36%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*