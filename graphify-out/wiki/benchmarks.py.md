# benchmarks.py

> 34 nodes

## Key Concepts

- **benchmarks.py** (18 connections) — `src/hal0/api/routes/benchmarks.py`
- **Any** (13 connections)
- **_store()** (9 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_plan()** (8 connections) — `src/hal0/api/routes/benchmarks.py`
- **post_queue()** (7 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_history()** (5 connections) — `src/hal0/api/routes/benchmarks.py`
- **list_runs()** (5 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_run()** (5 connections) — `src/hal0/api/routes/benchmarks.py`
- **_api_base()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **_run_summary()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_cells()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **post_control()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **post_run()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_events()** (4 connections) — `src/hal0/api/routes/benchmarks.py`
- **list_evals()** (3 connections) — `src/hal0/api/routes/benchmarks.py`
- **get_queue()** (3 connections) — `src/hal0/api/routes/benchmarks.py`
- **delete_queue()** (2 connections) — `src/hal0/api/routes/benchmarks.py`
- **Request** (1 connections)
- **StreamingResponse** (1 connections)
- **Benchmarks API — /api/benchmarks/* over the hal0.bench result store.  Endpoint s** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- **The hal0-api base the bench library should call back into for the     registry/h** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- **One Store per request. Cheap to construct (Store docstring) — no open     resour** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- **Flatten a raw records.jsonl record to the run-list row shape. The full     recor** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- **Current staleness report: what would run and why (design §6; the board's     Pla** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- **Filtered current-value matrix (compare view) — the per-lane x per-depth     mini** (1 connections) — `src/hal0/api/routes/benchmarks.py`
- *... and 9 more nodes in this community*

## Relationships

- [planner.py](planner.py.md) (5 shared connections)
- [cli.py](cli.py.md) (3 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [BadRequest](BadRequest.md) (3 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/benchmarks.py`

## Audit Trail

- EXTRACTED: 109 (92%)
- INFERRED: 10 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*