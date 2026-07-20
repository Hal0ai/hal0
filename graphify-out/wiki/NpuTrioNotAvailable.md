# NpuTrioNotAvailable

> 8 nodes · cohesion 0.25

## Key Concepts

- **NpuTrioNotAvailable** (8 connections) — `src/hal0/dispatcher/npu_trio.py`
- **test_dispatch_stt_raises_trio_unavailable_when_not_dispatchable()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **npu_trio.py** (4 connections) — `src/hal0/dispatcher/npu_trio.py`
- **test_dispatch_embed_raises_trio_unavailable()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **Hal0Error** (1 connections)
- **NPU trio direct-port dispatch.  A single ``flm serve`` process inside the contai** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **The NPU container isn't dispatchable, so the trio's shadow roles     (``flm-stt`** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **The surfaced error has to call out the user action.** (1 connections) — `tests/dispatcher/test_npu_trio.py`

## Relationships

- [NpuTrioRouter](NpuTrioRouter.md) (5 shared connections)
- [test_npu_trio.py](test_npu_trio.py.md) (4 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [test_dispatch_stt_raises_when_no_container_npu_slot](test_dispatch_stt_raises_when_no_container_npu_slot.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_trio.py`
- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 18 (72%)
- INFERRED: 7 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*