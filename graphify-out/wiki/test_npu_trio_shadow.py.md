# test_npu_trio_shadow.py

> 17 nodes

## Key Concepts

- **test_npu_trio_shadow.py** (10 connections) — `tests/slots/test_npu_trio_shadow.py`
- **_write_slot_toml()** (5 connections) — `tests/slots/test_npu_trio_shadow.py`
- **test_load_npu_shadow_does_not_spawn()** (5 connections) — `tests/slots/test_npu_trio_shadow.py`
- **_anchor()** (4 connections) — `tests/slots/test_npu_trio_shadow.py`
- **patched_spawn()** (4 connections) — `tests/slots/test_npu_trio_shadow.py`
- **test_load_npu_anchor_still_spawns()** (4 connections) — `tests/slots/test_npu_trio_shadow.py`
- **test_load_gpu_slot_still_spawns()** (4 connections) — `tests/slots/test_npu_trio_shadow.py`
- **_stt_shadow()** (3 connections) — `tests/slots/test_npu_trio_shadow.py`
- **_gpu_slot()** (3 connections) — `tests/slots/test_npu_trio_shadow.py`
- **test_is_npu_trio_shadow_predicate()** (2 connections) — `tests/slots/test_npu_trio_shadow.py`
- **Path** (1 connections)
- **MonkeyPatch** (1 connections)
- **NPU FLM trio *shadow* handling in SlotManager (shape-consolidation Unit 0).  The** (1 connections) — `tests/slots/test_npu_trio_shadow.py`
- **Record _spawn_locked calls and stub _await_ready so load() needs no I/O.      Th** (1 connections) — `tests/slots/test_npu_trio_shadow.py`
- **Loading an stt/embed shadow must NOT spawn a child (would 500 on NPU).** (1 connections) — `tests/slots/test_npu_trio_shadow.py`
- **The chat anchor (device=npu type=llm) is NOT a shadow — it must spawn.** (1 connections) — `tests/slots/test_npu_trio_shadow.py`
- **GPU/CPU slots are untouched by the trio-shadow guard.** (1 connections) — `tests/slots/test_npu_trio_shadow.py`

## Relationships

- [SlotManager](SlotManager.md) (4 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `tests/slots/test_npu_trio_shadow.py`

## Audit Trail

- EXTRACTED: 46 (90%)
- INFERRED: 5 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*