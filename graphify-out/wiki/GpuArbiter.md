# GpuArbiter

> 42 nodes · cohesion 0.09

## Key Concepts

- **GpuArbiter** (62 connections) — `src/hal0/slots/arbiter.py`
- **._load_state()** (15 connections) — `src/hal0/slots/arbiter.py`
- **gpu_exclusive_group()** (11 connections) — `src/hal0/slots/arbiter.py`
- **._persist()** (9 connections) — `src/hal0/slots/arbiter.py`
- **.ensure_img()** (8 connections) — `src/hal0/slots/arbiter.py`
- **.guard_dispatch()** (8 connections) — `src/hal0/slots/arbiter.py`
- **._idle_tick()** (8 connections) — `src/hal0/slots/arbiter.py`
- **.restore_llm()** (8 connections) — `src/hal0/slots/arbiter.py`
- **Any** (7 connections)
- **._cold_start_img()** (6 connections) — `src/hal0/slots/arbiter.py`
- **._slot_group()** (6 connections) — `src/hal0/slots/arbiter.py`
- **._refresh_group_cache()** (5 connections) — `src/hal0/slots/arbiter.py`
- **._rollback_to_llm()** (5 connections) — `src/hal0/slots/arbiter.py`
- **.status()** (5 connections) — `src/hal0/slots/arbiter.py`
- **.touch_img_activity()** (5 connections) — `src/hal0/slots/arbiter.py`
- **._read_slot_toml()** (4 connections) — `src/hal0/slots/arbiter.py`
- **._retry_after_s()** (4 connections) — `src/hal0/slots/arbiter.py`
- **.__init__()** (3 connections) — `src/hal0/slots/arbiter.py`
- **.mode()** (3 connections) — `src/hal0/slots/arbiter.py`
- **.run_idle_loop()** (3 connections) — `src/hal0/slots/arbiter.py`
- **.set_pin()** (3 connections) — `src/hal0/slots/arbiter.py`
- **.pinned()** (2 connections) — `src/hal0/slots/arbiter.py`
- **._resolve_alias()** (2 connections) — `src/hal0/slots/arbiter.py`
- **.saved_llm_slots()** (2 connections) — `src/hal0/slots/arbiter.py`
- **Path** (2 connections)
- *... and 17 more nodes in this community*

## Relationships

- [FakeManager](FakeManager.md) (30 shared connections)
- [arbiter.py](arbiter.py.md) (10 shared connections)
- [SlotConfigError](SlotConfigError.md) (4 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (3 shared connections)
- [v1.py](v1.py.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [GpuImageMode](GpuImageMode.md) (1 shared connections)

## Source Files

- `src/hal0/slots/arbiter.py`

## Audit Trail

- EXTRACTED: 199 (93%)
- INFERRED: 14 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*