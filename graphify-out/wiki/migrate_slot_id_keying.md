# migrate_slot_id_keying

> 25 nodes

## Key Concepts

- **migrate_slot_id_keying()** (18 connections) — `src/hal0/slots/migrate_id_keying.py`
- **migrate_id_keying.py** (12 connections) — `src/hal0/slots/migrate_id_keying.py`
- **SlotArtifactOps** (7 connections) — `src/hal0/slots/migrate_id_keying.py`
- **SubprocessSlotArtifactOps** (6 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Path** (5 connections)
- **_slot_table()** (5 connections) — `src/hal0/slots/migrate_id_keying.py`
- **_migrate_state()** (5 connections) — `src/hal0/slots/migrate_id_keying.py`
- **SlotMigration** (4 connections) — `src/hal0/slots/migrate_id_keying.py`
- **_read_raw_toml()** (4 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Any** (4 connections)
- **_toml_id()** (4 connections) — `src/hal0/slots/migrate_id_keying.py`
- **_write_json_atomic()** (4 connections) — `src/hal0/slots/migrate_id_keying.py`
- **MigrationReport** (3 connections) — `src/hal0/slots/migrate_id_keying.py`
- **.rename_unit()** (2 connections) — `src/hal0/slots/migrate_id_keying.py`
- **.rename_container()** (2 connections) — `src/hal0/slots/migrate_id_keying.py`
- **.rename_unit()** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **.rename_container()** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Collection** (1 connections)
- **One-shot M5 migration: name-keyed slot artefacts → id-keyed (rework §3.1).  Incr** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **The non-filesystem side effects of the migration.      Split behind a protocol s** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Deploy-only live ops: rename the Quadlet unit + podman container.      Best-effo** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **One slot that was migrated name → id in this run.** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **The table the flat slot fields live in — the ``[slot]`` sub-table when     prese** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Move ``<name>/state.json`` → ``<id>/state.json``, rewriting the ``name``     fie** (1 connections) — `src/hal0/slots/migrate_id_keying.py`
- **Migrate every name-keyed slot artefact under *config_dir* / *data_dir*     to id** (1 connections) — `src/hal0/slots/migrate_id_keying.py`

## Relationships

- [SlotIdentityStore](SlotIdentityStore.md) (5 shared connections)
- [RecordingSlotArtifactOps](RecordingSlotArtifactOps.md) (4 shared connections)
- [_container_runtime](_container_runtime.md) (2 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)
- [write_slot_toml](write_slot_toml.md) (1 shared connections)

## Source Files

- `src/hal0/slots/migrate_id_keying.py`

## Audit Trail

- EXTRACTED: 87 (92%)
- INFERRED: 8 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*