# test_dispatch_stt_raises_when_no_container_npu_slot

> 6 nodes · cohesion 0.33

## Key Concepts

- **test_dispatch_stt_raises_when_no_container_npu_slot()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **_slot_manager_with_noncontainer_npu()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_noncontainer_npu_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **npu slot without profile + without runtime=container → None.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **A non-container npu slot is NOT a trio backend — dispatch refuses.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **SlotManager mock for an npu slot that is NOT containerized.** (1 connections) — `tests/dispatcher/test_npu_trio.py`

## Relationships

- [test_npu_trio.py](test_npu_trio.py.md) (3 shared connections)
- [NpuTrioRouter](NpuTrioRouter.md) (2 shared connections)
- [NpuTrioNotAvailable](NpuTrioNotAvailable.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 13 (81%)
- INFERRED: 3 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*