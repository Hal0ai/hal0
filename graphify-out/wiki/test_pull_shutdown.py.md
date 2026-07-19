# test_pull_shutdown.py

> 21 nodes

## Key Concepts

- **test_pull_shutdown.py** (11 connections) — `tests/api/test_pull_shutdown.py`
- **test_startup_auto_resumes_interrupted_pull()** (8 connections) — `tests/api/test_pull_shutdown.py`
- **test_startup_does_not_auto_resume_a_completed_pull()** (7 connections) — `tests/api/test_pull_shutdown.py`
- **FastAPI** (6 connections)
- **_wait_for()** (6 connections) — `tests/api/test_pull_shutdown.py`
- **Any** (5 connections)
- **test_pull_request_returns_before_download_finishes()** (5 connections) — `tests/api/test_pull_shutdown.py`
- **test_shutdown_cancels_inflight_pull_within_bound()** (5 connections) — `tests/api/test_pull_shutdown.py`
- **test_pull_stream_closes_promptly_once_shutdown_flag_is_set()** (5 connections) — `tests/api/test_pull_shutdown.py`
- **hanging_run_pull()** (4 connections) — `tests/api/test_pull_shutdown.py`
- **app_isolated()** (3 connections) — `tests/api/test_pull_shutdown.py`
- **MonkeyPatch** (3 connections)
- **test_shutdown_with_no_active_pulls_is_a_no_op()** (3 connections) — `tests/api/test_pull_shutdown.py`
- **Issue #1225: `systemctl restart hal0-api` must not hang on a live model pull.  C** (1 connections) — `tests/api/test_pull_shutdown.py`
- **Patch run_pull with a fake that hangs until its task is cancelled.      Mirrors** (1 connections) — `tests/api/test_pull_shutdown.py`
- **The POST must not block on the (never-finishing) fake download —     proof that** (1 connections) — `tests/api/test_pull_shutdown.py`
- **Shutdown must cancel a still-running pull and finish promptly — the     core of** (1 connections) — `tests/api/test_pull_shutdown.py`
- **No in-flight pulls → shutdown doesn't wait on anything (regression     guard: th** (1 connections) — `tests/api/test_pull_shutdown.py`
- **A client (re)subscribing to the SSE progress stream while hal0-api is     shutti** (1 connections) — `tests/api/test_pull_shutdown.py`
- **A pull-job snapshot left non-terminal by a killed prior process (its     on-disk** (1 connections) — `tests/api/test_pull_shutdown.py`
- **A stale non-terminal snapshot for a model whose bytes already landed     on disk** (1 connections) — `tests/api/test_pull_shutdown.py`

## Relationships

- [create_app](create_app.md) (3 shared connections)
- [run_pull](run_pull.md) (2 shared connections)
- [pull.py](pull.py.md) (2 shared connections)
- [test_pull_routes.py](test_pull_routes.py.md) (2 shared connections)

## Source Files

- `tests/api/test_pull_shutdown.py`

## Audit Trail

- EXTRACTED: 70 (89%)
- INFERRED: 9 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*