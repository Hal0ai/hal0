# backends.py

> 33 nodes

## Key Concepts

- **backends.py** (16 connections) — `src/hal0/api/routes/backends.py`
- **_build_backend_payload()** (11 connections) — `src/hal0/api/routes/backends.py`
- **get_backend_details()** (8 connections) — `src/hal0/api/routes/backends.py`
- **load_npu_model()** (8 connections) — `src/hal0/api/routes/backends.py`
- **SlotManagerDep** (7 connections)
- **list_backends()** (7 connections) — `src/hal0/api/routes/backends.py`
- **Any** (6 connections)
- **unload_npu_model()** (6 connections) — `src/hal0/api/routes/backends.py`
- **_NoFreeSlotPort** (5 connections) — `src/hal0/api/routes/backends.py`
- **_loaded_children_for_backend()** (5 connections) — `src/hal0/api/routes/backends.py`
- **_allocate_npu_port()** (5 connections) — `src/hal0/api/routes/backends.py`
- **_npu_slot_name()** (4 connections) — `src/hal0/api/routes/backends.py`
- **_hardware_for_backend()** (4 connections) — `src/hal0/api/routes/backends.py`
- **_driver_for_backend()** (4 connections) — `src/hal0/api/routes/backends.py`
- **_mem_totals_for_backend()** (4 connections) — `src/hal0/api/routes/backends.py`
- **_state_for_backend()** (4 connections) — `src/hal0/api/routes/backends.py`
- **Request** (4 connections)
- **_sanitize_slot_suffix()** (3 connections) — `src/hal0/api/routes/backends.py`
- **Backend introspection endpoints.  Mounted under ``/api/backends`` (see :mod:`hal** (1 connections) — `src/hal0/api/routes/backends.py`
- **No free port available in the NPU dynamic-slot range.** (1 connections) — `src/hal0/api/routes/backends.py`
- **Map an FLM tag (e.g. ``qwen3.5:9b``) to a slot-name-safe suffix.      Slot names** (1 connections) — `src/hal0/api/routes/backends.py`
- **Slot name used for a given NPU-loaded model tag.** (1 connections) — `src/hal0/api/routes/backends.py`
- **Render the ``hardware`` field for a backend card.      Pulls from the cached har** (1 connections) — `src/hal0/api/routes/backends.py`
- **Return the driver / runtime string for the backend card.** (1 connections) — `src/hal0/api/routes/backends.py`
- **Return ``(memUsedMb, memTotalMb)`` best-effort.      ``memUsedMb`` is set to 0 i** (1 connections) — `src/hal0/api/routes/backends.py`
- *... and 8 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [catalog.py](catalog.py.md) (3 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/backends.py`

## Audit Trail

- EXTRACTED: 116 (92%)
- INFERRED: 10 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*