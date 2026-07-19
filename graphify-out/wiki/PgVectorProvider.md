# PgVectorProvider

> 52 nodes · cohesion 0.06

## Key Concepts

- **PgVectorProvider** (23 connections) — `src/hal0/memory/pgvector_provider.py`
- **provider_from_config()** (17 connections) — `src/hal0/memory/__init__.py`
- **test_pgvector_degrade_safety.py** (11 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_provider_factory.py** (9 connections) — `tests/memory/test_provider_factory.py`
- **_cfg()** (6 connections) — `tests/memory/test_provider_factory.py`
- **_build_hindsight_client()** (5 connections) — `src/hal0/memory/__init__.py`
- **test_factory_seeds_hindsight_graph_gate_from_config()** (5 connections) — `tests/memory/test_provider_factory.py`
- **__init__.py** (4 connections) — `src/hal0/memory/__init__.py`
- **pgvector_provider.py** (4 connections) — `src/hal0/memory/pgvector_provider.py`
- **test_factory_degrade_provider_has_degraded_true()** (4 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_factory_real_provider_degraded_is_falsy()** (4 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_factory_degrades_to_pgvector_when_hindsight_unavailable()** (4 connections) — `tests/memory/test_provider_factory.py`
- **test_factory_returns_pgvector_for_pgvector_engine()** (4 connections) — `tests/memory/test_provider_factory.py`
- **._allowed()** (3 connections) — `src/hal0/memory/pgvector_provider.py`
- **_cfg()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_hindsight_provider_degraded_is_falsy()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_pgvector_add_emits_warning_on_first_call()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_pgvector_add_still_stores_data_despite_warning()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_pgvector_add_warns_only_once_per_instance()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_pgvector_construction_emits_warning()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_pgvector_provider_degraded_is_true()** (3 connections) — `tests/memory/test_pgvector_degrade_safety.py`
- **test_factory_returns_hindsight_when_engine_hindsight()** (3 connections) — `tests/memory/test_provider_factory.py`
- **test_factory_unknown_engine_falls_back_to_hindsight()** (3 connections) — `tests/memory/test_provider_factory.py`
- **.from_env()** (2 connections) — `src/hal0/memory/hindsight_client.py`
- **Any** (2 connections)
- *... and 27 more nodes in this community*

## Relationships

- [MemoryProvider](MemoryProvider.md) (4 shared connections)
- [HindsightProvider](HindsightProvider.md) (3 shared connections)
- [types.py](types.py.md) (2 shared connections)
- [HindsightRestClient](HindsightRestClient.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [test_hindsight_provider.py](test_hindsight_provider.py.md) (1 shared connections)
- [_build](_build.md) (1 shared connections)
- [FakeMemoryProvider](FakeMemoryProvider.md) (1 shared connections)

## Source Files

- `src/hal0/memory/__init__.py`
- `src/hal0/memory/hindsight_client.py`
- `src/hal0/memory/pgvector_provider.py`
- `tests/memory/test_pgvector_degrade_safety.py`
- `tests/memory/test_provider_factory.py`

## Audit Trail

- EXTRACTED: 125 (75%)
- INFERRED: 41 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*