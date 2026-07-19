# MigrateState

> 65 nodes

## Key Concepts

- **MigrateState** (35 connections) — `src/hal0/memory/honcho_migrate.py`
- **migrate_hindsight_to_honcho()** (24 connections) — `src/hal0/memory/honcho_migrate.py`
- **test_honcho_migrate.py** (19 connections) — `tests/memory/test_honcho_migrate.py`
- **honcho_migrate.py** (16 connections) — `src/hal0/memory/honcho_migrate.py`
- **migrate_honcho_to_hindsight()** (16 connections) — `src/hal0/memory/honcho_migrate.py`
- **Client** (12 connections)
- **Any** (9 connections)
- **_run_forward_with_honcho()** (8 connections) — `tests/memory/test_honcho_migrate.py`
- **_hal0_pages()** (7 connections) — `tests/memory/test_honcho_migrate.py`
- **test_forward_migration_batches_and_ensures_resources()** (6 connections) — `tests/memory/test_honcho_migrate.py`
- **test_forward_migration_resume_skips_migrated_ids()** (6 connections) — `tests/memory/test_honcho_migrate.py`
- **.dataset_state()** (5 connections) — `src/hal0/memory/honcho_migrate.py`
- **_post_conclusions_batch()** (5 connections) — `src/hal0/memory/honcho_migrate.py`
- **_create_conclusions()** (5 connections) — `src/hal0/memory/honcho_migrate.py`
- **_hal0_list_page()** (5 connections) — `src/hal0/memory/honcho_migrate.py`
- **_honcho_handler()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **test_forward_migration_dry_run_writes_nothing()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **test_forward_migration_private_dataset_sets_private_header()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **_honcho_handler_with_conclusion_responses()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **test_reverse_migration_writes_hal0_and_skips_migration_sessions()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **test_reverse_migration_watermark_advances_and_dedupes_next_run()** (5 connections) — `tests/memory/test_honcho_migrate.py`
- **_honcho_client()** (4 connections) — `src/hal0/memory/honcho_migrate.py`
- **_hal0_client()** (4 connections) — `src/hal0/memory/honcho_migrate.py`
- **_hal0_add()** (4 connections) — `src/hal0/memory/honcho_migrate.py`
- **_list_conclusions_page()** (4 connections) — `src/hal0/memory/honcho_migrate.py`
- *... and 40 more nodes in this community*

## Relationships

- [Hal0Error](Hal0Error.md) (7 shared connections)
- [memory_migrate_commands.py](memory_migrate_commands.py.md) (3 shared connections)
- [test_memory_honcho_routes.py](test_memory_honcho_routes.py.md) (3 shared connections)
- [memory_admin.py](memory_admin.py.md) (1 shared connections)

## Source Files

- `src/hal0/memory/honcho_migrate.py`
- `tests/memory/test_honcho_migrate.py`

## Audit Trail

- EXTRACTED: 256 (84%)
- INFERRED: 48 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*