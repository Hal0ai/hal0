# npu_occupancy

> 21 nodes

## Key Concepts

- **npu_occupancy()** (13 connections) — `src/hal0/api/routes/npu.py`
- **npu.py** (9 connections) — `src/hal0/api/routes/npu.py`
- **get_swap_status()** (6 connections) — `src/hal0/api/routes/npu.py`
- **_slots_dir()** (4 connections) — `src/hal0/api/routes/npu.py`
- **_last_used_age_s()** (4 connections) — `src/hal0/api/routes/npu.py`
- **Any** (4 connections)
- **_occupancy_absent()** (4 connections) — `src/hal0/api/routes/npu.py`
- **_flm_footprint_gb()** (4 connections) — `src/hal0/api/routes/npu.py`
- **_model_tag()** (4 connections) — `src/hal0/api/routes/npu.py`
- **_map_slot_state()** (3 connections) — `src/hal0/api/routes/npu.py`
- **Request** (2 connections)
- **Path** (1 connections)
- **NPU-specific dashboard endpoints — PR-20.  Mounted under ``/api/npu`` (see :mod:** (1 connections) — `src/hal0/api/routes/npu.py`
- **Return /etc/hal0/slots/ — the TOML config directory.** (1 connections) — `src/hal0/api/routes/npu.py`
- **Seconds since *slot_name* last served a request, or ``None``.      Reads the ``a** (1 connections) — `src/hal0/api/routes/npu.py`
- **The minimal ``present:false`` payload (no NPU hw / no flm slot).** (1 connections) — `src/hal0/api/routes/npu.py`
- **Map a lower-cased :class:`SlotState` value to the card's 5 strings.      Contrac** (1 connections) — `src/hal0/api/routes/npu.py`
- **Resident footprint (GiB, 1 decimal) for *model_tag* from FLM's catalog.      Ret** (1 connections) — `src/hal0/api/routes/npu.py`
- **Strip a hal0 ``<tag>-FLM`` id back to FLM's native ``family:size`` tag.      Fal** (1 connections) — `src/hal0/api/routes/npu.py`
- **Return the honest NPU column-allocation + slot composition.      Read-only. Comp** (1 connections) — `src/hal0/api/routes/npu.py`
- **Return the current NPU trio chat-model swap-in-progress snapshot.      Response** (1 connections) — `src/hal0/api/routes/npu.py`

## Relationships

- [SlotState](SlotState.md) (2 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [npu_columns.py](npu_columns.py.md) (1 shared connections)
- [test_npu_swap_status.py](test_npu_swap_status.py.md) (1 shared connections)
- [NpuSwapStatus](NpuSwapStatus.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/npu.py`

## Audit Trail

- EXTRACTED: 60 (90%)
- INFERRED: 7 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*