# test_get_config_raises_resolves_none

> 4 nodes · cohesion 0.33

## Key Concepts

- **test_get_config_raises_resolves_none()** (3 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_no_slot_manager_resolves_none()** (3 connections) — `tests/dispatcher/test_npu_trio.py`
- **No slot_manager wired → None (the trio has nothing to observe).** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **get_config raising → swallowed, resolve degrades to None.** (1 connections) — `tests/dispatcher/test_npu_trio.py`

## Relationships

- [NpuTrioRouter](NpuTrioRouter.md) (2 shared connections)
- [test_npu_trio.py](test_npu_trio.py.md) (2 shared connections)

## Source Files

- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 6 (75%)
- INFERRED: 2 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*