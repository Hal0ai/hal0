# CapabilitySelection

> 66 nodes

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
- **read_schema_version()** (7 connections) — `src/hal0/capabilities/config.py`
- **_write_legacy_v1_file()** (7 connections) — `tests/config/test_schema_migration.py`
- **test_cli_migrate_vs_store_apply_no_lost_update()** (7 connections) — `tests/slot_config/test_store_locking.py`
- **capabilities_toml_payload()** (6 connections) — `src/hal0/capabilities/config.py`
- **capabilities_commands.py** (6 connections) — `src/hal0/cli/capabilities_commands.py`
- **TestAutoMigrate** (6 connections) — `tests/config/test_schema_migration.py`
- **.test_round_trip_write_v1_load_writes_v2()** (6 connections) — `tests/config/test_schema_migration.py`
- **.test_save_then_load_round_trip()** (6 connections) — `tests/config/test_schema_migration.py`
- **test_store_locking.py** (6 connections) — `tests/slot_config/test_store_locking.py`
- **_worker()** (6 connections) — `tests/slot_config/test_store_locking.py`
- **test_concurrent_apply_and_commit_keeps_both_updates()** (6 connections) — `tests/slot_config/test_store_locking.py`
- **Path** (5 connections)
- *... and 41 more nodes in this community*

## Relationships

- [.apply](apply.md) (9 shared connections)
- [map_backend_to_device](map_backend_to_device.md) (7 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (4 shared connections)
- [test_tts_capability_switch.py](test_tts_capability_switch.py.md) (4 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (4 shared connections)
- [file_lock](file_lock.md) (4 shared connections)
- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [BaseModel](BaseModel.md) (2 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (2 shared connections)
- [merge_slot_config](merge_slot_config.md) (2 shared connections)
- [test_store.py](test_store.py.md) (2 shared connections)
- [die](die.md) (2 shared connections)

## Source Files

- `src/hal0/capabilities/config.py`
- `src/hal0/capabilities/orchestrator.py`
- `src/hal0/cli/capabilities_commands.py`
- `tests/config/test_schema_migration.py`
- `tests/slot_config/test_store_locking.py`

## Audit Trail

- EXTRACTED: 219 (68%)
- INFERRED: 101 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*