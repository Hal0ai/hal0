# connect

> 87 nodes · cohesion 0.05

## Key Concepts

- **connect()** (111 connections) — `src/hal0/db/connection.py`
- **Path** (11 connections)
- **Path** (11 connections)
- **test_connection.py** (9 connections) — `tests/db/test_connection.py`
- **test_migrate_board.py** (8 connections) — `tests/db/test_migrate_board.py`
- **_db()** (8 connections) — `tests/db/test_migrate_board.py`
- **TestPackagedSlotsPortsMigration** (8 connections) — `tests/db/test_migrate_slots_ports.py`
- **Path** (7 connections)
- **Path** (7 connections)
- **_write_migration()** (7 connections) — `tests/db/test_migrate.py`
- **.test_cascade_does_not_fire_with_foreign_keys_off()** (6 connections) — `tests/db/test_connection.py`
- **TestMigrate** (6 connections) — `tests/db/test_migrate.py`
- **.test_migration_runs_inside_one_transaction()** (6 connections) — `tests/db/test_migrate.py`
- **_iso()** (6 connections) — `tests/metrics/test_retention.py`
- **TestPrune** (6 connections) — `tests/metrics/test_retention.py`
- **connection.py** (5 connections) — `src/hal0/db/connection.py`
- **TestCascadeDelete** (5 connections) — `tests/db/test_connection.py`
- **.test_cascade_fires_with_foreign_keys_on()** (5 connections) — `tests/db/test_connection.py`
- **test_migrate.py** (5 connections) — `tests/db/test_migrate.py`
- **test_card_status_foreign_key_enforced()** (5 connections) — `tests/db/test_migrate_board.py`
- **test_migrate_applies_version_5()** (5 connections) — `tests/db/test_migrate_board.py`
- **.test_applies_pending_migrations_in_order()** (5 connections) — `tests/db/test_migrate.py`
- **.test_partial_apply_only_picks_up_remaining()** (5 connections) — `tests/db/test_migrate.py`
- **TestPackagedStoreMigration** (5 connections) — `tests/db/test_migrate.py`
- **Path** (5 connections)
- *... and 62 more nodes in this community*

## Relationships

- [tx](tx.md) (22 shared connections)
- [applied_versions](applied_versions.md) (9 shared connections)
- [test_store_golden.py](test_store_golden.py.md) (8 shared connections)
- [Path](Path.md) (7 shared connections)
- [MetricsWriter](MetricsWriter.md) (4 shared connections)
- [aggregate_hour](aggregate_hour.md) (4 shared connections)
- [BoardStore](BoardStore.md) (3 shared connections)
- [models_health](models_health.md) (3 shared connections)
- [PortAuthority](PortAuthority.md) (3 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (3 shared connections)
- [Model](Model.md) (2 shared connections)
- [test_duplicate_refcount.py](test_duplicate_refcount.py.md) (2 shared connections)

## Source Files

- `src/hal0/board/store.py`
- `src/hal0/db/connection.py`
- `tests/db/test_connection.py`
- `tests/db/test_migrate.py`
- `tests/db/test_migrate_board.py`
- `tests/db/test_migrate_slots_ports.py`
- `tests/metrics/test_retention.py`

## Audit Trail

- EXTRACTED: 279 (65%)
- INFERRED: 153 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*