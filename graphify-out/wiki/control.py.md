# control.py

> 23 nodes

## Key Concepts

- **control.py** (16 connections) — `src/hal0/bench/control.py`
- **Any** (10 connections)
- **_write()** (8 connections) — `src/hal0/bench/control.py`
- **state_root()** (8 connections) — `src/hal0/bench/store.py`
- **_read()** (6 connections) — `src/hal0/bench/control.py`
- **read_queue()** (6 connections) — `src/hal0/bench/control.py`
- **_path()** (5 connections) — `src/hal0/bench/control.py`
- **read_control()** (5 connections) — `src/hal0/bench/control.py`
- **pop_next()** (5 connections) — `src/hal0/bench/control.py`
- **set_control()** (4 connections) — `src/hal0/bench/control.py`
- **enqueue()** (4 connections) — `src/hal0/bench/control.py`
- **dequeue()** (4 connections) — `src/hal0/bench/control.py`
- **worker_should_run()** (3 connections) — `src/hal0/bench/control.py`
- **read_status()** (3 connections) — `src/hal0/bench/control.py`
- **write_status()** (3 connections) — `src/hal0/bench/control.py`
- **Path** (3 connections)
- **.__init__()** (3 connections) — `src/hal0/bench/store.py`
- **.artifacts_dir()** (3 connections) — `src/hal0/bench/store.py`
- **control.py — web-driven run queue + worker control state.  The dashboard lets an** (1 connections) — `src/hal0/bench/control.py`
- **True iff the worker may drive a session right now (Start pressed).** (1 connections) — `src/hal0/bench/control.py`
- **Return (and remove) the head of the queue, or None if empty.** (1 connections) — `src/hal0/bench/control.py`
- **Resolve the state root: ``$BENCHLAB_STATE`` or the default. Read live (not     a** (1 connections) — `src/hal0/bench/store.py`
- **The per-run artifacts dir (raw llama-bench JSON, server_ab JSON, cell         lo** (1 connections) — `src/hal0/bench/store.py`

## Relationships

- [cli.py](cli.py.md) (3 shared connections)
- [runner.py](runner.py.md) (2 shared connections)
- [evalrun.py](evalrun.py.md) (2 shared connections)
- [test_probes.py](test_probes.py.md) (1 shared connections)

## Source Files

- `src/hal0/bench/control.py`
- `src/hal0/bench/store.py`

## Audit Trail

- EXTRACTED: 103 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*