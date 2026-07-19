# merge_slot_config

> 28 nodes

## Key Concepts

- **merge_slot_config()** (16 connections) — `src/hal0/slot_config/__init__.py`
- **test_merge_slot_config.py** (15 connections) — `tests/slot_config/test_merge_slot_config.py`
- **_write_caps_disabled_embed()** (5 connections) — `tests/slot_config/test_merge_slot_config.py`
- **_write_embed_slot()** (5 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_store_copy_safe_disable_still_writes()** (5 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_store_disable_does_not_rewrite_backend_device_model()** (5 connections) — `tests/slot_config/test_merge_slot_config.py`
- **_etc()** (4 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_one_level_deep_keeps_model_siblings()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_none_deletes_key()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_none_deletes_nested_key()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_folds_ctx_size_alias()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_folds_ctx_size_keeps_default_sibling()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_is_copy_safe_when_updates_carry_no_model()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_does_not_alias_base_model_on_merge_path()** (3 connections) — `tests/slot_config/test_merge_slot_config.py`
- **Path** (3 connections)
- **test_merge_none_deletes_missing_key_is_noop()** (2 connections) — `tests/slot_config/test_merge_slot_config.py`
- **test_merge_scalars_and_lists_replace_wholesale()** (2 connections) — `tests/slot_config/test_merge_slot_config.py`
- **Project ``updates`` onto a slot-config ``base`` dict, copy-safe.      THE one sh** (1 connections) — `src/hal0/slot_config/__init__.py`
- **SC-11: the one shared slot-projection merge primitive.  Both the store (:meth:`S** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **A partial ``{"model": {...}}`` update merges into the nested table,     not clob** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **An explicit ``None`` in updates DELETES the key (TOML has no null).      This is** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **Same None-deletes rule one level deep: {"server": {"extra_args": null}}     remo** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **Fresh ``ctx_size`` wins over a stale ``context_size`` seed, then the     alias i** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **manager.py dropped-sibling scenario: ``{"model": {"ctx_size": N}}``     must kee** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- **The load-bearing nuance: a pure non-model update (e.g. a disable) that     still** (1 connections) — `tests/slot_config/test_merge_slot_config.py`
- *... and 3 more nodes in this community*

## Relationships

- [unknown_slot_config_keys](unknown_slot_config_keys.md) (4 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (2 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/slot_config/__init__.py`
- `tests/slot_config/test_merge_slot_config.py`

## Audit Trail

- EXTRACTED: 72 (77%)
- INFERRED: 22 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*