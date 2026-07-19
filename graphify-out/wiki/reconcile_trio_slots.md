# reconcile_trio_slots

> 11 nodes · cohesion 0.27

## Key Concepts

- **reconcile_trio_slots()** (8 connections) — `src/hal0/slots/npu/trio.py`
- **NpuTrioHost** (7 connections) — `src/hal0/slots/npu/trio.py`
- **trio.py** (4 connections) — `src/hal0/slots/npu/trio.py`
- **.create()** (3 connections) — `src/hal0/slots/npu/trio.py`
- **.iter_configs()** (3 connections) — `src/hal0/slots/npu/trio.py`
- **Any** (3 connections)
- **._invalidate_cfg_cache()** (2 connections) — `src/hal0/slots/npu/trio.py`
- **Protocol** (1 connections)
- **NPU FLM-trio shadow reconciler (P3-slots §1d).  The NPU runs a single ``flm serv** (1 connections) — `src/hal0/slots/npu/trio.py`
- **Narrow seam :func:`reconcile_trio_slots` needs from ``SlotManager``.** (1 connections) — `src/hal0/slots/npu/trio.py`
- **Startup pass: reconcile the FLM-trio shadow slots to canon.      The NPU runs on** (1 connections) — `src/hal0/slots/npu/trio.py`

## Relationships

- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [is_container_npu_cfg](is_container_npu_cfg.md) (1 shared connections)
- [write_slot_toml](write_slot_toml.md) (1 shared connections)

## Source Files

- `src/hal0/slots/npu/trio.py`

## Audit Trail

- EXTRACTED: 32 (94%)
- INFERRED: 2 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*