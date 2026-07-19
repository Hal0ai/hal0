# cmd_worker

> 31 nodes · cohesion 0.13

## Key Concepts

- **cmd_worker()** (20 connections) — `src/hal0/bench/cli.py`
- **control.py** (16 connections) — `src/hal0/bench/control.py`
- **_worker_eval()** (10 connections) — `src/hal0/bench/cli.py`
- **Any** (10 connections)
- **_write()** (8 connections) — `src/hal0/bench/control.py`
- **state_root()** (8 connections) — `src/hal0/bench/store.py`
- **read_queue()** (7 connections) — `src/hal0/bench/control.py`
- **_read()** (6 connections) — `src/hal0/bench/control.py`
- **read_control()** (6 connections) — `src/hal0/bench/control.py`
- **dequeue()** (5 connections) — `src/hal0/bench/control.py`
- **enqueue()** (5 connections) — `src/hal0/bench/control.py`
- **_path()** (5 connections) — `src/hal0/bench/control.py`
- **pop_next()** (5 connections) — `src/hal0/bench/control.py`
- **worker_should_run()** (5 connections) — `src/hal0/bench/control.py`
- **set_control()** (4 connections) — `src/hal0/bench/control.py`
- **write_status()** (4 connections) — `src/hal0/bench/control.py`
- **_eval_run_id()** (3 connections) — `src/hal0/bench/cli.py`
- **_resolve_model_id()** (3 connections) — `src/hal0/bench/cli.py`
- **read_status()** (3 connections) — `src/hal0/bench/control.py`
- **Path** (3 connections)
- **.artifacts_dir()** (3 connections) — `src/hal0/bench/store.py`
- **.__init__()** (3 connections) — `src/hal0/bench/store.py`
- **Map a queued model reference to a registry id the planner can select. The     da** (1 connections) — `src/hal0/bench/cli.py`
- **A STABLE run_id for a queued eval, derived from the queue item id.      The suit** (1 connections) — `src/hal0/bench/cli.py`
- **Run the full agentic-eval task set for one queued model (the dashboard's     Too** (1 connections) — `src/hal0/bench/cli.py`
- *... and 6 more nodes in this community*

## Relationships

- [cli.py](cli.py.md) (13 shared connections)
- [evalrun.py](evalrun.py.md) (5 shared connections)
- [runner.py](runner.py.md) (4 shared connections)
- [suites.py](suites.py.md) (2 shared connections)
- [planner.py](planner.py.md) (2 shared connections)
- [test_probes.py](test_probes.py.md) (1 shared connections)

## Source Files

- `src/hal0/bench/cli.py`
- `src/hal0/bench/control.py`
- `src/hal0/bench/store.py`

## Audit Trail

- EXTRACTED: 149 (99%)
- INFERRED: 2 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*