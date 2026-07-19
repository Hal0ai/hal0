# GpuImageMode

> 25 nodes

## Key Concepts

- **GpuImageMode** (17 connections) — `src/hal0/slots/arbiter.py`
- **_RecordingSlotManager** (11 connections) — `tests/api/test_v1_backend_aware_load.py`
- **_run_chat()** (11 connections) — `tests/api/test_v1_backend_aware_load.py`
- **test_v1_backend_aware_load.py** (9 connections) — `tests/api/test_v1_backend_aware_load.py`
- **MonkeyPatch** (6 connections)
- **_ImageModeArbiter** (6 connections) — `tests/api/test_v1_backend_aware_load.py`
- **test_alias_load_refused_during_image_mode()** (6 connections) — `tests/api/test_v1_backend_aware_load.py`
- **test_slot_backed_model_loads_before_dispatch()** (5 connections) — `tests/api/test_v1_backend_aware_load.py`
- **test_unbacked_model_does_not_load()** (5 connections) — `tests/api/test_v1_backend_aware_load.py`
- **test_dispatch_proceeds_even_if_backend_aware_load_fails()** (5 connections) — `tests/api/test_v1_backend_aware_load.py`
- **_patch_alias()** (4 connections) — `tests/api/test_v1_backend_aware_load.py`
- **.iter_configs()** (2 connections) — `tests/api/test_v1_backend_aware_load.py`
- **Any** (2 connections)
- **.guard_dispatch()** (2 connections) — `tests/api/test_v1_backend_aware_load.py`
- **LLM dispatch refused — the GPU is in exclusive image mode.** (1 connections) — `src/hal0/slots/arbiter.py`
- **.__init__()** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **.load()** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **#430 — backend-aware load for slot-backed models, path-independent.  A model req** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **Records ``load`` calls; ``iter_configs`` unused (alias map is patched).** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **POST /v1/chat/completions for ``model``; return (response, order).      ``order`** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **A by-name request for a model whose owning slot declares a device     backend dr** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **A by-name request for a model with NO backing slot kicks no     backend-aware lo** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **A failing backend-aware load is swallowed: dispatch still runs and     decides t** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **Stands in for GpuArbiter while mode == img.** (1 connections) — `tests/api/test_v1_backend_aware_load.py`
- **D4 (route-level): the backend-aware lazy-load must NEVER pull an LLM     back on** (1 connections) — `tests/api/test_v1_backend_aware_load.py`

## Relationships

- [_ArbiterSlotManager](_ArbiterSlotManager.md) (5 shared connections)
- [FakeManager](FakeManager.md) (4 shared connections)
- [Dispatcher](Dispatcher.md) (3 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [arbiter.py](arbiter.py.md) (1 shared connections)
- [GpuArbiter](GpuArbiter.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)
- [test_prewire_smoke.py](test_prewire_smoke.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/slots/arbiter.py`
- `tests/api/test_v1_backend_aware_load.py`

## Audit Trail

- EXTRACTED: 81 (79%)
- INFERRED: 21 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*