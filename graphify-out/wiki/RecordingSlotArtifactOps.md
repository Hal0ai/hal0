# RecordingSlotArtifactOps

> 16 nodes

## Key Concepts

- **RecordingSlotArtifactOps** (8 connections) — `src/hal0/slots/migrate_id_keying.py`
- **test_slot_id_keying.py** (7 connections) — `tests/migration/test_slot_id_keying.py`
- **Path** (6 connections)
- **_identity()** (6 connections) — `tests/migration/test_slot_id_keying.py`
- **test_migration_is_idempotent()** (6 connections) — `tests/migration/test_slot_id_keying.py`
- **test_partial_state_rolls_forward()** (6 connections) — `tests/migration/test_slot_id_keying.py`
- **test_all_surfaces_end_up_id_keyed()** (5 connections) — `tests/migration/test_slot_id_keying.py`
- **_snapshot()** (4 connections) — `tests/migration/test_slot_id_keying.py`
- **tree()** (3 connections) — `tests/migration/test_slot_id_keying.py`
- **.rename_unit()** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **.rename_container()** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Test / dry-run double: records the requested renames, does nothing.** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **One-shot M5 slot-id-keying migration (rework §3.1 / PR4).  Feeds the migrator a** (1 connections) — `tests/migration/test_slot_id_keying.py`
- **A name-keyed on-disk slot tree: config TOMLs + state.json files.** (1 connections) — `tests/migration/test_slot_id_keying.py`
- **Byte snapshot of every file under the tree (for idempotence checks).** (1 connections) — `tests/migration/test_slot_id_keying.py`
- **A crash between the TOML move and the state.json move leaves a     half-migrated** (1 connections) — `tests/migration/test_slot_id_keying.py`

## Relationships

- [migrate_slot_id_keying](migrate_slot_id_keying.md) (4 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (2 shared connections)

## Source Files

- `src/hal0/slots/migrate_id_keying.py`
- `tests/migration/test_slot_id_keying.py`

## Audit Trail

- EXTRACTED: 48 (83%)
- INFERRED: 10 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*