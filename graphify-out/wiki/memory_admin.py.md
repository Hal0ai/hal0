# memory_admin.py

> 32 nodes

## Key Concepts

- **memory_admin.py** (20 connections) — `src/hal0/api/routes/memory_admin.py`
- **MemoryUnavailable** (12 connections) — `src/hal0/api/routes/memory.py`
- **_client()** (10 connections) — `src/hal0/api/routes/memory_admin.py`
- **Any** (10 connections)
- **delete_bank()** (10 connections) — `src/hal0/api/routes/memory_admin.py`
- **bank_subgraph()** (8 connections) — `src/hal0/api/routes/memory_admin.py`
- **bank_document_transfer_import()** (8 connections) — `src/hal0/api/routes/memory_admin.py`
- **Request** (7 connections)
- **MemoryEngineUnreachable** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **MemoryEngineError** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **_validate_segments()** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **_forward()** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **engine_status()** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **_raise_transfer_error()** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **bank_document_transfer_export()** (6 connections) — `src/hal0/api/routes/memory_admin.py`
- **MemoryEngineUnsupported** (5 connections) — `src/hal0/api/routes/memory_admin.py`
- **MemoryEngineShape** (5 connections) — `src/hal0/api/routes/memory_admin.py`
- **_read_body()** (5 connections) — `src/hal0/api/routes/memory_admin.py`
- **_delete_preview()** (4 connections) — `src/hal0/api/routes/memory_admin.py`
- **_probe()** (3 connections) — `src/hal0/api/routes/memory_admin.py`
- **_require()** (3 connections) — `src/hal0/api/routes/memory_admin.py`
- **The memory engine failed to initialise at boot.      Returned when the API got f** (1 connections) — `src/hal0/api/routes/memory.py`
- **Exception** (1 connections)
- **Response** (1 connections)
- **_make_handler()** (1 connections) — `src/hal0/api/routes/memory_admin.py`
- *... and 7 more nodes in this community*

## Relationships

- [Hal0Error](Hal0Error.md) (8 shared connections)
- [BadRequest](BadRequest.md) (4 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [HindsightProvider](HindsightProvider.md) (2 shared connections)
- [record_action](record_action.md) (2 shared connections)
- [MigrateState](MigrateState.md) (1 shared connections)
- [MemoryNamespaceError](MemoryNamespaceError.md) (1 shared connections)
- [comfyui_switchover](comfyui_switchover.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/memory.py`
- `src/hal0/api/routes/memory_admin.py`

## Audit Trail

- EXTRACTED: 141 (87%)
- INFERRED: 22 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*