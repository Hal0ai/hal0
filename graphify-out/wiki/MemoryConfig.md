# MemoryConfig

> 28 nodes · cohesion 0.10

## Key Concepts

- **MemoryConfig** (17 connections) — `src/hal0/config/schema.py`
- **_build()** (11 connections) — `tests/api/test_memory_gate.py`
- **TestMemoryAgentProviders** (7 connections) — `tests/config/test_honcho_schema.py`
- **test_memory_gate.py** (6 connections) — `tests/api/test_memory_gate.py`
- **test_memory_engine_field.py** (4 connections) — `tests/config/test_memory_engine_field.py`
- **test_memory_enabled_by_default()** (3 connections) — `tests/api/test_memory_gate.py`
- **test_status_exposes_memory_enabled_as_bool()** (3 connections) — `tests/api/test_memory_gate.py`
- **FastAPI** (2 connections)
- **test_memory_disabled_when_config_says_so()** (2 connections) — `tests/api/test_memory_gate.py`
- **.test_agent_private_flag()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_agent_providers_rejects_unknown_value()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_defaults_empty()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_engine_unaffected_by_agent_providers()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_hal0_config_carries_agent_providers()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_valid_provider_values()** (2 connections) — `tests/config/test_honcho_schema.py`
- **test_engine_accepts_known_engines()** (2 connections) — `tests/config/test_memory_engine_field.py`
- **test_engine_defaults_to_hindsight()** (2 connections) — `tests/config/test_memory_engine_field.py`
- **test_engine_rejects_unknown()** (2 connections) — `tests/config/test_memory_engine_field.py`
- **test_config_default_unified_bank_true()** (2 connections) — `tests/memory/test_unified_bank.py`
- **._agent_providers_known()** (1 connections) — `src/hal0/config/schema.py`
- **._engine_is_known()** (1 connections) — `src/hal0/config/schema.py`
- **[memory] section of hal0.toml.      Container for the per-subsystem memory tunab** (1 connections) — `src/hal0/config/schema.py`
- **TestClient** (1 connections)
- **Memory gate — ``[memory].enabled`` toggles the whole subsystem.  The memory engi** (1 connections) — `tests/api/test_memory_gate.py`
- **Build a fresh app + client with ``[memory].enabled`` set (or left at     its sch** (1 connections) — `tests/api/test_memory_gate.py`
- *... and 3 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (3 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [_build](_build.md) (1 shared connections)
- [MemoryGraphConfig](MemoryGraphConfig.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [HonchoConfig](HonchoConfig.md) (1 shared connections)
- [test_unified_bank.py](test_unified_bank.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/api/test_memory_gate.py`
- `tests/config/test_honcho_schema.py`
- `tests/config/test_memory_engine_field.py`
- `tests/memory/test_unified_bank.py`

## Audit Trail

- EXTRACTED: 59 (70%)
- INFERRED: 25 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*