# SlotConfigStore

> 61 nodes · cohesion 0.08

## Key Concepts

- **SlotConfigStore** (37 connections) — `src/hal0/slot_config/__init__.py`
- **test_store.py** (22 connections) — `tests/slot_config/test_store.py`
- **_selection()** (18 connections) — `tests/slot_config/test_store.py`
- **_write_caps()** (17 connections) — `tests/slot_config/test_store.py`
- **_write_embed_slot()** (17 connections) — `tests/slot_config/test_store.py`
- **.apply()** (11 connections) — `src/hal0/slot_config/__init__.py`
- **ChangeSet** (10 connections) — `src/hal0/slot_config/__init__.py`
- **_read_toml()** (9 connections) — `tests/slot_config/test_store.py`
- **_etc()** (8 connections) — `tests/slot_config/test_store.py`
- **test_failed_mid_commit_leaves_disk_at_before()** (8 connections) — `tests/slot_config/test_store.py`
- **.apply_and_commit()** (7 connections) — `src/hal0/slot_config/__init__.py`
- **test_commit_writes_after_to_disk()** (7 connections) — `tests/slot_config/test_store.py`
- **test_commit_writes_enabled_false_to_disk_on_disable()** (7 connections) — `tests/slot_config/test_store.py`
- **Path** (6 connections)
- **.transaction()** (6 connections) — `src/hal0/slot_config/__init__.py`
- **_write_state()** (6 connections) — `src/hal0/slot_config/__init__.py`
- **test_apply_before_matches_disk()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_preserves_model_siblings_and_folds_ctx_alias()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_reconciles_slot_fields_for_npu()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_skips_slot_toml_when_missing()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_writes_enabled_false_on_disable()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_writes_enabled_true_on_enable()** (6 connections) — `tests/slot_config/test_store.py`
- **test_apply_writes_nothing()** (6 connections) — `tests/slot_config/test_store.py`
- **test_commit_then_reapply_is_noop()** (6 connections) — `tests/slot_config/test_store.py`
- **test_revert_removes_file_that_was_absent()** (6 connections) — `tests/slot_config/test_store.py`
- *... and 36 more nodes in this community*

## Relationships

- [unknown_slot_config_keys](unknown_slot_config_keys.md) (11 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (6 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (4 shared connections)
- [write_slot_toml](write_slot_toml.md) (4 shared connections)
- [test_tts_capability_switch.py](test_tts_capability_switch.py.md) (4 shared connections)
- [merge_slot_config](merge_slot_config.md) (2 shared connections)
- [.__init__](__init__.md) (1 shared connections)
- [file_lock](file_lock.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `src/hal0/slot_config/__init__.py`
- `tests/slot_config/test_store.py`

## Audit Trail

- EXTRACTED: 275 (85%)
- INFERRED: 49 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*