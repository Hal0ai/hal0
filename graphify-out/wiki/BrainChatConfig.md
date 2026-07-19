# BrainChatConfig

> 33 nodes · cohesion 0.08

## Key Concepts

- **BrainChatConfig** (23 connections) — `src/hal0/config/schema.py`
- **test_schema.py** (13 connections) — `tests/config/test_schema.py`
- **ServerConfig** (9 connections) — `src/hal0/config/schema.py`
- **TestBrainChatConfig** (7 connections) — `tests/config/test_schema.py`
- **TestSeededSlotTomls** (5 connections) — `tests/config/test_schema.py`
- **TestServerConfigEnv** (5 connections) — `tests/config/test_schema.py`
- **test_gated_call_fails_closed_without_queue()** (4 connections) — `tests/brain/test_brain_injection.py`
- **_declared_provider()** (4 connections) — `tests/config/test_schema.py`
- **.test_seeded_slot_profile_resolves_to_live_seed()** (4 connections) — `tests/config/test_schema.py`
- **.test_seeded_slot_provider_is_valid()** (4 connections) — `tests/config/test_schema.py`
- **Path** (3 connections)
- **.test_present_on_hal0config_by_default()** (3 connections) — `tests/config/test_schema.py`
- **.test_duplicate_upstream_names_raise()** (3 connections) — `tests/config/test_schema.py`
- **._env_keys_and_values_sane()** (2 connections) — `src/hal0/config/schema.py`
- **.test_completion_timeout_must_be_positive()** (2 connections) — `tests/config/test_schema.py`
- **.test_defaults_are_safe_and_stable()** (2 connections) — `tests/config/test_schema.py`
- **.test_guardrail_flags_round_trip_from_toml()** (2 connections) — `tests/config/test_schema.py`
- **.test_max_rounds_bounds_enforced()** (2 connections) — `tests/config/test_schema.py`
- **.test_model_override_round_trips()** (2 connections) — `tests/config/test_schema.py`
- **.test_env_default_none()** (2 connections) — `tests/config/test_schema.py`
- **.test_invalid_env_key_rejected()** (2 connections) — `tests/config/test_schema.py`
- **.test_newline_in_value_rejected()** (2 connections) — `tests/config/test_schema.py`
- **.test_valid_env_accepted()** (2 connections) — `tests/config/test_schema.py`
- **TestUpstreamsConfig** (2 connections) — `tests/config/test_schema.py`
- **[server] section in a slot TOML.      Currently carries only ``extra_args`` — a** (1 connections) — `src/hal0/config/schema.py`
- *... and 8 more nodes in this community*

## Relationships

- [schema.py](schema.py.md) (7 shared connections)
- [load_hal0_config](load_hal0_config.md) (6 shared connections)
- [test_board_chat.py](test_board_chat.py.md) (2 shared connections)
- [test_board_chat_tool_use_e2e.py](test_board_chat_tool_use_e2e.py.md) (2 shared connections)
- [test_brain_injection.py](test_brain_injection.py.md) (2 shared connections)
- [test_brain_read_only.py](test_brain_read_only.py.md) (2 shared connections)
- [test_brain_resilience.py](test_brain_resilience.py.md) (2 shared connections)
- [SlotConfig](SlotConfig.md) (2 shared connections)
- [UpstreamEntry](UpstreamEntry.md) (2 shared connections)
- [chat.py](chat.py.md) (1 shared connections)
- [test_board_chat_admin_tools.py](test_board_chat_admin_tools.py.md) (1 shared connections)
- [test_brain_ctx_precheck.py](test_brain_ctx_precheck.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/brain/test_brain_injection.py`
- `tests/config/test_schema.py`

## Audit Trail

- EXTRACTED: 76 (64%)
- INFERRED: 42 (36%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*