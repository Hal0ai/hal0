# _SpyStore

> 9 nodes · cohesion 0.22

## Key Concepts

- **_SpyStore** (14 connections) — `tests/board/test_hermes_executor.py`
- **A BoardStore that records any call to a canonical-state mutator.      lane / dep** (1 connections) — `tests/board/test_hermes_executor.py`
- **.add_link()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.delete_task()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.__init__()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.reassign()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.reclaim()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.remove_link()** (1 connections) — `tests/board/test_hermes_executor.py`
- **.update_task()** (1 connections) — `tests/board/test_hermes_executor.py`

## Relationships

- [AttemptHandle](AttemptHandle.md) (2 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (2 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)

## Source Files

- `tests/board/test_hermes_executor.py`

## Audit Trail

- EXTRACTED: 19 (86%)
- INFERRED: 3 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*