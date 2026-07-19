# _build

> 12 nodes · cohesion 0.23

## Key Concepts

- **_build()** (9 connections) — `tests/api/test_memory_degraded_status.py`
- **test_memory_degraded_status.py** (6 connections) — `tests/api/test_memory_degraded_status.py`
- **test_status_memory_degraded_true_for_pgvector_fallback()** (4 connections) — `tests/api/test_memory_degraded_status.py`
- **test_status_exposes_memory_degraded_field()** (3 connections) — `tests/api/test_memory_degraded_status.py`
- **test_status_memory_degraded_false_for_real_provider()** (3 connections) — `tests/api/test_memory_degraded_status.py`
- **test_status_memory_degraded_none_when_disabled()** (3 connections) — `tests/api/test_memory_degraded_status.py`
- **TestClient** (2 connections)
- **#613 — /api/status must expose memory_degraded for operator visibility.  Verifie** (1 connections) — `tests/api/test_memory_degraded_status.py`
- **/api/status always carries a memory_degraded field.** (1 connections) — `tests/api/test_memory_degraded_status.py`
- **memory_degraded=None when no memory provider is wired.** (1 connections) — `tests/api/test_memory_degraded_status.py`
- **memory_degraded=True when PgVectorProvider (in-memory fallback) is wired.** (1 connections) — `tests/api/test_memory_degraded_status.py`
- **memory_degraded=False when a durable provider (no degraded attr) is wired.** (1 connections) — `tests/api/test_memory_degraded_status.py`

## Relationships

- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [MemoryConfig](MemoryConfig.md) (1 shared connections)
- [PgVectorProvider](PgVectorProvider.md) (1 shared connections)

## Source Files

- `tests/api/test_memory_degraded_status.py`

## Audit Trail

- EXTRACTED: 30 (86%)
- INFERRED: 5 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*