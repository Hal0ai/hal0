# HonchoConfig

> 34 nodes

## Key Concepts

- **HonchoLLMFeatureConfig** (9 connections) — `src/hal0/config/schema.py`
- **HonchoConfig** (9 connections) — `src/hal0/config/schema.py`
- **apply_honcho_env()** (7 connections) — `src/hal0/memory/honcho_env.py`
- **honcho_env.py** (6 connections) — `src/hal0/memory/honcho_env.py`
- **HonchoLLMConfig** (5 connections) — `src/hal0/config/schema.py`
- **render_env()** (5 connections) — `src/hal0/memory/honcho_env.py`
- **test_honcho_schema.py** (5 connections) — `tests/config/test_honcho_schema.py`
- **TestHonchoDefaults** (5 connections) — `tests/config/test_honcho_schema.py`
- **_resolve()** (4 connections) — `src/hal0/memory/honcho_env.py`
- **_emit_model_config()** (4 connections) — `src/hal0/memory/honcho_env.py`
- **_is_missing_unit_error()** (4 connections) — `src/hal0/memory/honcho_env.py`
- **.test_top_level_default()** (4 connections) — `tests/config/test_honcho_schema.py`
- **TestHonchoNameValidators** (4 connections) — `tests/config/test_honcho_schema.py`
- **.test_llm_feature_defaults()** (3 connections) — `tests/config/test_honcho_schema.py`
- **TestHonchoTransportValidator** (3 connections) — `tests/config/test_honcho_schema.py`
- **.test_round_trips()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_hal0_config_round_trips_with_honcho()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_valid_transports()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_invalid_transport_rejected()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_valid_workspace_and_peer_names()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_invalid_workspace_rejected()** (2 connections) — `tests/config/test_honcho_schema.py`
- **.test_invalid_user_peer_rejected()** (2 connections) — `tests/config/test_honcho_schema.py`
- **._transport_known()** (1 connections) — `src/hal0/config/schema.py`
- **One Honcho LLM feature route (``deriver``/``dialectic``/``summary``/     ``dream** (1 connections) — `src/hal0/config/schema.py`
- **``[honcho.llm]`` block — per-feature model routing for the Honcho stack.** (1 connections) — `src/hal0/config/schema.py`
- *... and 9 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (4 shared connections)
- [BaseModel](BaseModel.md) (3 shared connections)
- [schema.py](schema.py.md) (3 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [memory_migrate_commands.py](memory_migrate_commands.py.md) (1 shared connections)
- [MemoryConfig](MemoryConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/memory/honcho_env.py`
- `tests/config/test_honcho_schema.py`

## Audit Trail

- EXTRACTED: 80 (78%)
- INFERRED: 23 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*