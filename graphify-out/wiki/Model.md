# Model

> 95 nodes · cohesion 0.04

## Key Concepts

- **Model** (63 connections) — `src/hal0/registry/model.py`
- **pull.py** (46 connections) — `src/hal0/registry/pull.py`
- **_download_one()** (22 connections) — `src/hal0/registry/pull.py`
- **Path** (21 connections)
- **persist_pull_job()** (16 connections) — `src/hal0/registry/pull.py`
- **run_flm_pull()** (16 connections) — `src/hal0/registry/pull.py`
- **pull_job_file()** (15 connections) — `src/hal0/registry/pull.py`
- **PullJob** (15 connections) — `src/hal0/registry/pull.py`
- **FileSetPlan** (14 connections) — `src/hal0/registry/fileset.py`
- **_run_pull_fileset()** (14 connections) — `src/hal0/registry/pull.py`
- **FileSetEntry** (13 connections) — `src/hal0/registry/fileset.py`
- **_tmp_dir()** (13 connections) — `src/hal0/registry/pull.py`
- **PullError** (12 connections) — `src/hal0/registry/pull.py`
- **_register_pulled_fileset()** (11 connections) — `src/hal0/registry/pull.py`
- **_final_path_for_entry()** (10 connections) — `src/hal0/registry/pull.py`
- **PullJobNotFound** (10 connections) — `src/hal0/registry/pull.py`
- **PullFile** (9 connections) — `src/hal0/registry/pull.py`
- **PullInvalidSource** (9 connections) — `src/hal0/registry/pull.py`
- **_pull_jobs_dir()** (8 connections) — `src/hal0/registry/pull.py`
- **_sanitise_id()** (8 connections) — `src/hal0/registry/pull.py`
- **_PullCancelled** (7 connections) — `src/hal0/registry/pull.py`
- **PullChecksumMismatch** (7 connections) — `src/hal0/registry/pull.py`
- **PullInsufficientDisk** (7 connections) — `src/hal0/registry/pull.py`
- **_final_path()** (6 connections) — `src/hal0/registry/pull.py`
- **list_persisted_jobs()** (6 connections) — `src/hal0/registry/pull.py`
- *... and 70 more nodes in this community*

## Relationships

- [run_pull](run_pull.md) (27 shared connections)
- [ModelRegistry](ModelRegistry.md) (27 shared connections)
- [_pull_root](_pull_root.md) (18 shared connections)
- [models.py](models.py.md) (6 shared connections)
- [plan_fileset](plan_fileset.md) (5 shared connections)
- [ModelDefaults](ModelDefaults.md) (5 shared connections)
- [get_curated](get_curated.md) (5 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (5 shared connections)
- [tx](tx.md) (5 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (4 shared connections)
- [flm.py](flm.py.md) (4 shared connections)
- [migrate-haloai.py](migrate-haloai.py.md) (3 shared connections)

## Source Files

- `src/hal0/registry/fileset.py`
- `src/hal0/registry/model.py`
- `src/hal0/registry/pull.py`
- `tests/registry/test_pull.py`

## Audit Trail

- EXTRACTED: 368 (70%)
- INFERRED: 156 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*