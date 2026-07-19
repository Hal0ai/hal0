# Path

> 44 nodes

## Key Concepts

- **Path** (11 connections)
- **applied_versions()** (9 connections) — `src/hal0/db/migrate.py`
- **migrate()** (9 connections) — `src/hal0/db/migrate.py`
- **.test_partial_apply_only_picks_up_002_when_001_already_applied()** (8 connections) — `tests/metrics/test_migration.py`
- **_write_migration()** (7 connections) — `tests/db/test_migrate.py`
- **migrate.py** (6 connections) — `src/hal0/db/migrate.py`
- **_ensure_migrations_table()** (6 connections) — `src/hal0/db/migrate.py`
- **TestMigrate** (6 connections) — `tests/db/test_migrate.py`
- **.test_migration_runs_inside_one_transaction()** (6 connections) — `tests/db/test_migrate.py`
- **TestMetricsMigration** (6 connections) — `tests/metrics/test_migration.py`
- **_discover_migrations()** (5 connections) — `src/hal0/db/migrate.py`
- **test_migrate.py** (5 connections) — `tests/db/test_migrate.py`
- **.test_applies_pending_migrations_in_order()** (5 connections) — `tests/db/test_migrate.py`
- **.test_partial_apply_only_picks_up_remaining()** (5 connections) — `tests/db/test_migrate.py`
- **TestPackagedStoreMigration** (5 connections) — `tests/db/test_migrate.py`
- **Path** (5 connections)
- **_split_statements()** (4 connections) — `src/hal0/db/migrate.py`
- **.test_idempotent_second_call_applies_nothing()** (4 connections) — `tests/db/test_migrate.py`
- **.test_forward_only_never_reapplies_lower_version()** (4 connections) — `tests/db/test_migrate.py`
- **TestPackagedRegistryMigration** (4 connections) — `tests/db/test_migrate.py`
- **Connection** (3 connections)
- **.test_001_registry_creates_expected_tables()** (3 connections) — `tests/db/test_migrate.py`
- **.test_001_registry_is_idempotent_against_the_real_package()** (3 connections) — `tests/db/test_migrate.py`
- **.test_003_store_applies_on_top_of_001()** (3 connections) — `tests/db/test_migrate.py`
- **.test_003_store_blob_refcount_roundtrip()** (3 connections) — `tests/db/test_migrate.py`
- *... and 19 more nodes in this community*

## Relationships

- [connect](connect.md) (18 shared connections)

## Source Files

- `src/hal0/db/migrate.py`
- `tests/db/test_migrate.py`
- `tests/metrics/test_migration.py`

## Audit Trail

- EXTRACTED: 136 (82%)
- INFERRED: 30 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*