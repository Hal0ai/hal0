# CapabilitySelection

> 63 nodes · cohesion 0.07

## Key Concepts

- **CapabilitySelection** (27 connections) — `src/hal0/capabilities/config.py`
- **auto_migrate_capabilities_file()** (16 connections) — `src/hal0/capabilities/config.py`
- **load_capabilities_config()** (16 connections) — `src/hal0/capabilities/config.py`
- **CapabilityConfig** (14 connections) — `src/hal0/capabilities/config.py`
- **capabilities_toml_path()** (13 connections) — `src/hal0/capabilities/config.py`
- **save_capabilities_config()** (13 connections) — `src/hal0/capabilities/config.py`
- **config.py** (11 connections) — `src/hal0/capabilities/config.py`
- **migrate()** (10 connections) — `src/hal0/cli/capabilities_commands.py`
- **test_schema_migration.py** (10 connections) — `tests/config/test_schema_migration.py`
- **.initialize_if_missing()** (9 connections) — `src/hal0/capabilities/orchestrator.py`
- **_cli_migrate_worker()** (9 connections) — `tests/slot_config/test_store_locking.py`
- **Path** (8 connections)
- **capabilities_v1_backup_path()** (7 connections) — `src/hal0/capabilities/config.py`
- **._reconciled_capabilities()** (7 connections) — `src/hal0/slot_config/__init__.py`
- **_write_legacy_v1_file()** (7 connections) — `tests/config/test_schema_migration.py`
- **test_cli_migrate_vs_store_apply_no_lost_update()** (7 connections) — `tests/slot_config/test_store_locking.py`
- **_worker()** (7 connections) — `tests/slot_config/test_store_locking.py`
- **capabilities_toml_payload()** (6 connections) — `src/hal0/capabilities/config.py`
- **capabilities_commands.py** (6 connections) — `src/hal0/cli/capabilities_commands.py`
- **TestAutoMigrate** (6 connections) — `tests/config/test_schema_migration.py`
- **.test_round_trip_write_v1_load_writes_v2()** (6 connections) — `tests/config/test_schema_migration.py`
- **.test_save_then_load_round_trip()** (6 connections) — `tests/config/test_schema_migration.py`
- **test_store_locking.py** (6 connections) — `tests/slot_config/test_store_locking.py`
- **test_concurrent_apply_and_commit_keeps_both_updates()** (6 connections) — `tests/slot_config/test_store_locking.py`
- **Path** (5 connections)
- *... and 38 more nodes in this community*

## Relationships

- [map_backend_to_device](map_backend_to_device.md) (9 shared connections)
- [.apply](apply.md) (9 shared connections)
- [SlotConfigStore](SlotConfigStore.md) (6 shared connections)
- [file_lock](file_lock.md) (4 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (4 shared connections)
- [test_tts_capability_switch.py](test_tts_capability_switch.py.md) (4 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (2 shared connections)
- [merge_slot_config](merge_slot_config.md) (2 shared connections)
- [die](die.md) (2 shared connections)
- [catalog.py](catalog.py.md) (2 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (2 shared connections)

## Source Files

- `src/hal0/capabilities/config.py`
- `src/hal0/capabilities/orchestrator.py`
- `src/hal0/cli/capabilities_commands.py`
- `src/hal0/slot_config/__init__.py`
- `tests/config/test_schema_migration.py`
- `tests/slot_config/test_store_locking.py`

## Audit Trail

- EXTRACTED: 215 (69%)
- INFERRED: 98 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*