# pull_jobs.py

> 27 nodes

## Key Concepts

- **pull_jobs.py** (13 connections) — `src/hal0/registry/pull_jobs.py`
- **Any** (7 connections)
- **run_pull_with_events()** (7 connections) — `src/hal0/registry/pull_jobs.py`
- **start_flm_pull()** (7 connections) — `src/hal0/registry/pull_jobs.py`
- **resolve_pull_source()** (6 connections) — `src/hal0/registry/pull_jobs.py`
- **resolve_pull_source_with_body()** (6 connections) — `src/hal0/registry/pull_jobs.py`
- **load_persisted()** (5 connections) — `src/hal0/registry/pull_jobs.py`
- **Request** (5 connections)
- **resolve_pull_capability()** (5 connections) — `src/hal0/registry/pull_jobs.py`
- **schedule_pull_task()** (5 connections) — `src/hal0/registry/pull_jobs.py`
- **PullJob** (5 connections)
- **emit_terminal_pull_event()** (5 connections) — `src/hal0/registry/pull_jobs.py`
- **reconcile_persisted()** (4 connections) — `src/hal0/registry/pull_jobs.py`
- **speed_bps()** (3 connections) — `src/hal0/registry/pull_jobs.py`
- **eta_s()** (3 connections) — `src/hal0/registry/pull_jobs.py`
- **HuggingFace/FLM pull-job orchestration (extracted from routes/models.py).  This** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Read a persisted pull-job snapshot from disk, or None if absent/unreadable.** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Repair a persisted snapshot that was left in a non-terminal state.      A snapsh** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Resolve the (hf_repo, hf_file) tuple for a pull.      Priority:       1. The reg** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Resolve ``(capability, comfyui_subdir)`` for a pull (P3 grouped layout).      Pr** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Resolve (hf_repo, hf_file, mmproj_file) with an optional body override.      Ret** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Launch a pull body as a detached ``asyncio.Task``, tracked for shutdown.      De** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Wrap ``run_pull`` so footer-visible progress events fan out.      Emits ``pull.p** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Emit the success/failure/cancellation footer event for a pull.      Shared betwe** (1 connections) — `src/hal0/registry/pull_jobs.py`
- **Approximate average bytes/s since the job started.** (1 connections) — `src/hal0/registry/pull_jobs.py`
- *... and 2 more nodes in this community*

## Relationships

- [get_curated](get_curated.md) (3 shared connections)
- [pull.py](pull.py.md) (3 shared connections)
- [Model](Model.md) (2 shared connections)
- [run_pull](run_pull.md) (2 shared connections)
- [Path](Path.md) (1 shared connections)
- [evalrun.py](evalrun.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/pull_jobs.py`

## Audit Trail

- EXTRACTED: 89 (91%)
- INFERRED: 9 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*