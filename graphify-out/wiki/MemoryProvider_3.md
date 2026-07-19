# MemoryProvider

> 34 nodes · cohesion 0.07

## Key Concepts

- **MemoryProvider** (30 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **Any** (8 connections)
- **test_provider_abc.py** (7 connections) — `tests/memory/test_provider_abc.py`
- **test_concrete_providers_are_memory_providers()** (4 connections) — `tests/memory/test_provider_abc.py`
- **memory_provider.py** (3 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.get_tool_schemas()** (3 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **test_memory_provider_roster_is_frozen()** (2 connections) — `tests/agents/hermes/test_contract_compatibility.py`
- **.get_config_schema()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.handle_tool_call()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.initialize()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.is_available()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_memory_write()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_pre_compress()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_session_end()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.save_config()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.sync_turn()** (2 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **ABC** (2 connections)
- **test_abc_optional_methods_have_safe_defaults()** (2 connections) — `tests/memory/test_provider_abc.py`
- **.backup_paths()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_delegation()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_session_switch()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.on_turn_start()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.prefetch()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.queue_prefetch()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- **.shutdown()** (1 connections) — `tests/fixtures/hermes/contracts/memory_provider.py`
- *... and 9 more nodes in this community*

## Relationships

- [test_contract_compatibility.py](test_contract_compatibility.py.md) (3 shared connections)
- [MemoryProvider](MemoryProvider.md) (2 shared connections)
- [Hal0MemoryProvider](Hal0MemoryProvider.md) (2 shared connections)
- [Hal0MemoryClient](Hal0MemoryClient.md) (1 shared connections)
- [Dispatcher](Dispatcher.md) (1 shared connections)
- [HindsightProvider](HindsightProvider.md) (1 shared connections)
- [PgVectorProvider](PgVectorProvider.md) (1 shared connections)

## Source Files

- `tests/agents/hermes/test_contract_compatibility.py`
- `tests/fixtures/hermes/contracts/memory_provider.py`
- `tests/memory/test_provider_abc.py`

## Audit Trail

- EXTRACTED: 83 (87%)
- INFERRED: 12 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*