# test_schema_seeds_c5.py

> 17 nodes · cohesion 0.15

## Key Concepts

- **test_schema_seeds_c5.py** (9 connections) — `tests/config/test_schema_seeds_c5.py`
- **_load_seed_slot()** (7 connections) — `tests/config/test_schema_seeds_c5.py`
- **MemoryEmbeddingConfig** (6 connections) — `src/hal0/config/schema.py`
- **test_brain_seed_ships_ready()** (3 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_seed_slot_ports_are_mutually_unique()** (3 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_seed_toml_ships_clean()** (3 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_cognee_era_embedding_keys_are_dropped_silently()** (2 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_rerank_defaults_are_hindsight_era()** (2 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_seed_rerank_toml_validates()** (2 connections) — `tests/config/test_schema_seeds_c5.py`
- **test_seed_utility_toml_validates()** (2 connections) — `tests/config/test_schema_seeds_c5.py`
- **[memory.embedding] section of hal0.toml — Hindsight-era rerank knobs.      ADR-0** (1 connections) — `src/hal0/config/schema.py`
- **Path** (1 connections)
- **Tests for Phase C5 — rerank + utility seed TOMLs and reranker defaults.** (1 connections) — `tests/config/test_schema_seeds_c5.py`
- **Clean-seed invariant (WS-E, #1107): every shipped seed ships DISABLED with     n** (1 connections) — `tests/config/test_schema_seeds_c5.py`
- **The brain steward is the deliberate exception to the clean-seed rule     (#1258)** (1 connections) — `tests/config/test_schema_seeds_c5.py`
- **Validate a shipped seed TOML into a SlotConfig (top-level or [slot]-nested).** (1 connections) — `tests/config/test_schema_seeds_c5.py`
- **No two shipped seed slot TOMLs may bind the same port.      Deconfliction today** (1 connections) — `tests/config/test_schema_seeds_c5.py`

## Relationships

- [SlotConfig](SlotConfig.md) (4 shared connections)
- [schema.py](schema.py.md) (2 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_schema_seeds_c5.py`

## Audit Trail

- EXTRACTED: 44 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*