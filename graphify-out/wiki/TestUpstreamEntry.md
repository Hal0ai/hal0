# TestUpstreamEntry

> 55 nodes

## Key Concepts

- **TestUpstreamEntry** (16 connections) — `tests/config/test_schema.py`
- **test_schema.py** (13 connections) — `tests/config/test_schema.py`
- **ModelConfig** (9 connections) — `src/hal0/config/schema.py`
- **ServerConfig** (9 connections) — `src/hal0/config/schema.py`
- **ProviderEntry** (9 connections) — `src/hal0/config/schema.py`
- **TestServerConfigEnv** (5 connections) — `tests/config/test_schema.py`
- **TestModelConfig** (5 connections) — `tests/config/test_schema.py`
- **TestSeededSlotTomls** (5 connections) — `tests/config/test_schema.py`
- **TestProviderEntry** (4 connections) — `tests/config/test_schema.py`
- **_declared_provider()** (4 connections) — `tests/config/test_schema.py`
- **.test_seeded_slot_provider_is_valid()** (4 connections) — `tests/config/test_schema.py`
- **.test_seeded_slot_profile_resolves_to_live_seed()** (4 connections) — `tests/config/test_schema.py`
- **TestProvidersConfig** (3 connections) — `tests/config/test_schema.py`
- **.test_round_trip()** (3 connections) — `tests/config/test_schema.py`
- **Path** (3 connections)
- **._env_keys_and_values_sane()** (2 connections) — `src/hal0/config/schema.py`
- **.test_valid_env_accepted()** (2 connections) — `tests/config/test_schema.py`
- **.test_env_default_none()** (2 connections) — `tests/config/test_schema.py`
- **.test_invalid_env_key_rejected()** (2 connections) — `tests/config/test_schema.py`
- **.test_newline_in_value_rejected()** (2 connections) — `tests/config/test_schema.py`
- **.test_defaults()** (2 connections) — `tests/config/test_schema.py`
- **.test_context_size_below_minimum_raises()** (2 connections) — `tests/config/test_schema.py`
- **.test_context_size_minimum_ok()** (2 connections) — `tests/config/test_schema.py`
- **.test_negative_rope_freq_base_raises()** (2 connections) — `tests/config/test_schema.py`
- **.test_requires_catalog_id()** (2 connections) — `tests/config/test_schema.py`
- *... and 30 more nodes in this community*

## Relationships

- [schema.py](schema.py.md) (4 shared connections)
- [ConfigParseError](ConfigParseError.md) (4 shared connections)
- [BaseModel](BaseModel.md) (3 shared connections)
- [SlotConfig](SlotConfig.md) (3 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [HardwareInfo](HardwareInfo.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_schema.py`

## Audit Trail

- EXTRACTED: 118 (78%)
- INFERRED: 33 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*