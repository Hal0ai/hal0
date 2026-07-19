# test_chat_normalization.py

> 29 nodes · cohesion 0.11

## Key Concepts

- **test_chat_normalization.py** (27 connections) — `tests/api/test_chat_normalization.py`
- **_make_request()** (20 connections) — `tests/api/test_chat_normalization.py`
- **test_container_slot_not_treated_as_remote_for_thinking()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_hal0_brain_alias_resolves_via_kind_slot_upstream_o21()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_loaded_models_includes_kind_slot_upstreams_o21()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_loaded_models_includes_ready_container_slots()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_normalize_chat_body_caches_canonical_messages_in_request_body()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_normalize_chat_body_collapses_stacked_systems()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_normalize_chat_body_hoists_mid_array_system_to_position_zero()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_normalize_chat_body_user_only_body_is_passthrough()** (3 connections) — `tests/api/test_chat_normalization.py`
- **test_caller_top_level_thinking_translated_to_kwarg()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_chat_template_kwargs_opt_out_through_seam()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_normalize_loaded_models_uses_cache_no_rpc()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_per_slot_default_overridden_by_request()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_per_slot_enable_thinking_default_applied()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_physical_model_passthrough_still_gets_thinking()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_remote_model_not_thinking_injected()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_request_body_rewritten_for_downstream_consumers()** (2 connections) — `tests/api/test_chat_normalization.py`
- **test_virtual_name_resolved_and_thinking_injected()** (2 connections) — `tests/api/test_chat_normalization.py`
- **Build a minimal request stand-in.      ``loaded`` materialises as a container-ba** (1 connections) — `tests/api/test_chat_normalization.py`
- **The loaded set is read from the cached upstream catalogs only —     no live /v1/** (1 connections) — `tests/api/test_chat_normalization.py`
- **The loaded set derives from container-backed upstreams (``slot_name``     set):** (1 connections) — `tests/api/test_chat_normalization.py`
- **O21 regression: SlotManager registers LOCAL container slots as     kind="slot" (** (1 connections) — `tests/api/test_chat_normalization.py`
- **End-to-end O21 repro: [brain_chat] model="hal0/brain" with the brain     slot lo** (1 connections) — `tests/api/test_chat_normalization.py`
- **Container slots register as kind='remote' (with slot_name) but are LOCAL —     t** (1 connections) — `tests/api/test_chat_normalization.py`
- *... and 4 more nodes in this community*

## Relationships

- [_Headers](_Headers.md) (8 shared connections)
- [_SlotManager](_SlotManager.md) (2 shared connections)
- [types.py](types.py.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_normalization.py`

## Audit Trail

- EXTRACTED: 99 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*