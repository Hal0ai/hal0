# MemoryGraphConfig

> 20 nodes · cohesion 0.13

## Key Concepts

- **MemoryGraphConfig** (11 connections) — `src/hal0/config/schema.py`
- **.test_top_level_default()** (5 connections) — `tests/config/test_memory_graph_schema.py`
- **test_memory_graph_schema.py** (4 connections) — `tests/config/test_memory_graph_schema.py`
- **TestMemoryGraphDefaults** (4 connections) — `tests/config/test_memory_graph_schema.py`
- **TestExtractionSlotGrammar** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **TestLegacyKeyDrop** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_legacy_keys_dropped_via_hal0_config()** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_legacy_route_and_upstream_keys_silently_dropped()** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_disabled_round_trips()** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_legacy_route_upstream_keys_are_no_longer_emitted()** (3 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_invalid_slot_names_rejected()** (2 connections) — `tests/config/test_memory_graph_schema.py`
- **.test_valid_slot_names()** (2 connections) — `tests/config/test_memory_graph_schema.py`
- **.extraction_slot_grammar()** (1 connections) — `src/hal0/config/schema.py`
- **[memory.graph] section of hal0.toml (ADR-0023).      Controls graph extraction o** (1 connections) — `src/hal0/config/schema.py`
- **Unit tests for the ADR-0023 [memory.graph] schema.  ADR-0023 replaced the inert** (1 connections) — `tests/config/test_memory_graph_schema.py`
- **Hal0Config carries an off-by-default memory.graph section.** (1 connections) — `tests/config/test_memory_graph_schema.py`
- **An off-by-default block must round-trip cleanly.** (1 connections) — `tests/config/test_memory_graph_schema.py`
- **The dumped block carries only the ADR-0023 fields.** (1 connections) — `tests/config/test_memory_graph_schema.py`
- **An old hal0.toml block with ``route``/``upstream`` loads cleanly and         tho** (1 connections) — `tests/config/test_memory_graph_schema.py`
- **Same legacy block nested under a full Hal0Config load drops cleanly.** (1 connections) — `tests/config/test_memory_graph_schema.py`

## Relationships

- [schema.py](schema.py.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [memory.py](memory.py.md) (1 shared connections)
- [MemoryConfig](MemoryConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_memory_graph_schema.py`

## Audit Trail

- EXTRACTED: 42 (78%)
- INFERRED: 12 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*