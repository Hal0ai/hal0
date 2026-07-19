# test_slot_config_validation.py

> 22 nodes

## Key Concepts

- **test_slot_config_validation.py** (14 connections) — `tests/api/test_slot_config_validation.py`
- **Path** (13 connections)
- **._next_free_slot_port()** (10 connections) — `src/hal0/capabilities/orchestrator.py`
- **TestClient** (8 connections)
- **_read()** (6 connections) — `tests/api/test_slot_config_validation.py`
- **test_put_config_valid_keys_and_dynamic_server_fields_pass()** (5 connections) — `tests/api/test_slot_config_validation.py`
- **test_put_config_tolerated_extras_pass()** (5 connections) — `tests/api/test_slot_config_validation.py`
- **test_next_free_slot_port_pool_capped_below_comfyui()** (5 connections) — `tests/api/test_slot_config_validation.py`
- **test_put_config_unknown_top_level_key_400()** (4 connections) — `tests/api/test_slot_config_validation.py`
- **test_patch_defaults_ctx_size_alias_still_accepted()** (4 connections) — `tests/api/test_slot_config_validation.py`
- **test_next_free_slot_port_exhausted_configured_range_raises()** (4 connections) — `tests/api/test_slot_config_validation.py`
- **test_put_config_unknown_model_key_400_with_path()** (3 connections) — `tests/api/test_slot_config_validation.py`
- **test_patch_defaults_unknown_key_400()** (3 connections) — `tests/api/test_slot_config_validation.py`
- **test_create_unknown_key_400_writes_nothing()** (3 connections) — `tests/api/test_slot_config_validation.py`
- **test_create_flat_body_valid_keys_201()** (3 connections) — `tests/api/test_slot_config_validation.py`
- **test_next_free_slot_port_honors_hal0_toml_range()** (3 connections) — `tests/api/test_slot_config_validation.py`
- **slot_toml()** (2 connections) — `tests/api/test_slot_config_validation.py`
- **Pick a free port in the configured slot range.          Delegates to the shared** (1 connections) — `src/hal0/capabilities/orchestrator.py`
- **Boundary validation on slot-config writes (PUT config / PATCH defaults / POST cr** (1 connections) — `tests/api/test_slot_config_validation.py`
- **[server].env (schema-derived, not hardcoded) + extra_args pass.** (1 connections) — `tests/api/test_slot_config_validation.py`
- **default_voice (voice settings) and string image override keep working.** (1 connections) — `tests/api/test_slot_config_validation.py`
- **The auto-allocation pool deliberately ends at 8099 (#1036).      The pool defaul** (1 connections) — `tests/api/test_slot_config_validation.py`

## Relationships

- [.apply](apply.md) (3 shared connections)
- [test_slots_policy.py](test_slots_policy.py.md) (2 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (1 shared connections)

## Source Files

- `src/hal0/capabilities/orchestrator.py`
- `tests/api/test_slot_config_validation.py`

## Audit Trail

- EXTRACTED: 90 (90%)
- INFERRED: 10 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*