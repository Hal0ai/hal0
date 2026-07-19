# load_hal0_config

> 32 nodes

## Key Concepts

- **load_hal0_config()** (54 connections) — `src/hal0/config/loader.py`
- **Hal0Config** (49 connections) — `src/hal0/config/schema.py`
- **save_hal0_config()** (25 connections) — `src/hal0/config/loader.py`
- **BrainChatConfig** (23 connections) — `src/hal0/config/schema.py`
- **TestHal0ConfigRoundTrip** (8 connections) — `tests/config/test_loader.py`
- **TestBrainChatConfig** (7 connections) — `tests/config/test_schema.py`
- **test_pull_root_uses_store_when_set()** (6 connections) — `tests/registry/test_pull_store.py`
- **.test_load_with_explicit_path()** (5 connections) — `tests/config/test_loader.py`
- **.test_persisted_schema_version_loads_back()** (5 connections) — `tests/config/test_loader.py`
- **test_pull_root_defaults_to_pull_root_when_store_unset()** (5 connections) — `tests/registry/test_pull_store.py`
- **test_require_auth_env_override_beats_persisted_config()** (4 connections) — `tests/api/test_auth_core.py`
- **test_gated_call_fails_closed_without_queue()** (4 connections) — `tests/brain/test_brain_injection.py`
- **.test_save_then_load()** (4 connections) — `tests/config/test_loader.py`
- **test_persist_store_dir_idempotent_when_both_already_set()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_persist_store_dir_rewrites_flm_store_when_store_already_set()** (4 connections) — `tests/install/test_orchestrate.py`
- **.test_load_default_when_file_missing()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_with_invalid_toml_raises()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_with_invalid_field_value_raises()** (3 connections) — `tests/config/test_loader.py`
- **.test_present_on_hal0config_by_default()** (3 connections) — `tests/config/test_schema.py`
- **Path** (3 connections)
- **.test_defaults_are_safe_and_stable()** (2 connections) — `tests/config/test_schema.py`
- **.test_model_override_round_trips()** (2 connections) — `tests/config/test_schema.py`
- **.test_guardrail_flags_round_trip_from_toml()** (2 connections) — `tests/config/test_schema.py`
- **.test_max_rounds_bounds_enforced()** (2 connections) — `tests/config/test_schema.py`
- **.test_completion_timeout_must_be_positive()** (2 connections) — `tests/config/test_schema.py`
- *... and 7 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (16 shared connections)
- [settings.py](settings.py.md) (12 shared connections)
- [Hal0Error](Hal0Error.md) (7 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (7 shared connections)
- [updater.py](updater.py.md) (5 shared connections)
- [schema.py](schema.py.md) (5 shared connections)
- [Path](Path.md) (5 shared connections)
- [test_auth_core.py](test_auth_core.py.md) (4 shared connections)
- [_build](_build.md) (4 shared connections)
- [test_board_chat.py](test_board_chat.py.md) (4 shared connections)
- [test_brain_resilience.py](test_brain_resilience.py.md) (4 shared connections)
- [HonchoConfig](HonchoConfig.md) (4 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `tests/api/test_auth_core.py`
- `tests/brain/test_brain_injection.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema.py`
- `tests/install/test_orchestrate.py`
- `tests/registry/test_pull_store.py`

## Audit Trail

- EXTRACTED: 79 (33%)
- INFERRED: 160 (67%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*