# run_migrations

> 42 nodes

## Key Concepts

- **run_migrations()** (15 connections) — `src/hal0/config/migrations/__init__.py`
- **TestMigrations** (11 connections) — `tests/config/test_loader.py`
- **latest_version()** (10 connections) — `src/hal0/config/migrations/__init__.py`
- **__init__.py** (9 connections) — `src/hal0/config/migrations/__init__.py`
- **config_migrate()** (7 connections) — `src/hal0/cli/config_commands.py`
- **_maybe_run_config_migrations()** (7 connections) — `src/hal0/updater/updater.py`
- **test_config_migrate.py** (5 connections) — `tests/cli/test_config_migrate.py`
- **test_migrate_already_latest_does_not_rewrite()** (5 connections) — `tests/cli/test_config_migrate.py`
- **MigrationError** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **_read_schema_version()** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **_deep_copy_dict()** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **_set_home()** (4 connections) — `tests/cli/test_config_migrate.py`
- **Path** (4 connections)
- **test_migrate_runs_the_real_runner_when_behind()** (4 connections) — `tests/cli/test_config_migrate.py`
- **register()** (3 connections) — `src/hal0/config/migrations/__init__.py`
- **Any** (3 connections)
- **test_migrate_no_config_is_honest()** (3 connections) — `tests/cli/test_config_migrate.py`
- **.test_run_migrations_identity_for_v1()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_unversioned_input_stamps_v1()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_missing_step_raises()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_chained()** (3 connections) — `tests/config/test_loader.py`
- **.test_latest_version_at_least_1()** (2 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_rejects_downgrade()** (2 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_does_not_mutate_input()** (2 connections) — `tests/config/test_loader.py`
- **Migrate hal0.toml forward to the latest config schema version.      Reads ``meta** (1 connections) — `src/hal0/cli/config_commands.py`
- *... and 17 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (5 shared connections)
- [updater.py](updater.py.md) (3 shared connections)
- [Enum](Enum.md) (2 shared connections)
- [die](die.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `src/hal0/cli/config_commands.py`
- `src/hal0/config/migrations/__init__.py`
- `src/hal0/updater/updater.py`
- `tests/cli/test_config_migrate.py`
- `tests/config/test_loader.py`

## Audit Trail

- EXTRACTED: 102 (74%)
- INFERRED: 36 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*