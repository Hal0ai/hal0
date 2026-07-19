# applied_versions

> 24 nodes · cohesion 0.13

## Key Concepts

- **applied_versions()** (9 connections) — `src/hal0/db/migrate.py`
- **migrate()** (9 connections) — `src/hal0/db/migrate.py`
- **.test_partial_apply_only_picks_up_002_when_001_already_applied()** (8 connections) — `tests/metrics/test_migration.py`
- **migrate.py** (6 connections) — `src/hal0/db/migrate.py`
- **_ensure_migrations_table()** (6 connections) — `src/hal0/db/migrate.py`
- **TestMetricsMigration** (6 connections) — `tests/metrics/test_migration.py`
- **_discover_migrations()** (5 connections) — `src/hal0/db/migrate.py`
- **Path** (5 connections)
- **_split_statements()** (4 connections) — `src/hal0/db/migrate.py`
- **Connection** (3 connections)
- **.test_002_applies_after_001_creates_expected_tables()** (3 connections) — `tests/metrics/test_migration.py`
- **.test_002_is_idempotent()** (3 connections) — `tests/metrics/test_migration.py`
- **.test_request_metric_row_round_trips()** (3 connections) — `tests/metrics/test_migration.py`
- **.test_slot_sample_composite_primary_key()** (3 connections) — `tests/metrics/test_migration.py`
- **Path** (2 connections)
- **test_migration.py** (2 connections) — `tests/metrics/test_migration.py`
- **Forward-only SQLite schema migration runner.  This is the concrete implementatio** (1 connections) — `src/hal0/db/migrate.py`
- **Create the bookkeeping table if absent. Idempotent.** (1 connections) — `src/hal0/db/migrate.py`
- **Return the set of migration version numbers already applied.** (1 connections) — `src/hal0/db/migrate.py`
- **Split a migration file into individual statements on ``;``.      ``sqlite3.Curso** (1 connections) — `src/hal0/db/migrate.py`
- **Return ``(version, filename, sql_text)`` triples, sorted by version.      Reads** (1 connections) — `src/hal0/db/migrate.py`
- **Apply every not-yet-applied migration, in ascending version order.      Safe to** (1 connections) — `src/hal0/db/migrate.py`
- **002_metrics.sql migration tests -- applies cleanly on top of 001, in order.** (1 connections) — `tests/metrics/test_migration.py`
- **A DB that already has 001 applied (e.g. by SqliteModelRegistry)         picks up** (1 connections) — `tests/metrics/test_migration.py`

## Relationships

- [connect](connect.md) (9 shared connections)
- [tx](tx.md) (2 shared connections)

## Source Files

- `src/hal0/db/migrate.py`
- `tests/metrics/test_migration.py`

## Audit Trail

- EXTRACTED: 68 (80%)
- INFERRED: 17 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*