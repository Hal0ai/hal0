# run_migrations

> 31 nodes · cohesion 0.10

## Key Concepts

- **run_migrations()** (15 connections) — `src/hal0/config/migrations/__init__.py`
- **TestMigrations** (11 connections) — `tests/config/test_loader.py`
- **latest_version()** (10 connections) — `src/hal0/config/migrations/__init__.py`
- **__init__.py** (9 connections) — `src/hal0/config/migrations/__init__.py`
- **_maybe_run_config_migrations()** (7 connections) — `src/hal0/updater/updater.py`
- **_deep_copy_dict()** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **MigrationError** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **_read_schema_version()** (4 connections) — `src/hal0/config/migrations/__init__.py`
- **Any** (3 connections)
- **register()** (3 connections) — `src/hal0/config/migrations/__init__.py`
- **.test_run_migrations_chained()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_identity_for_v1()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_missing_step_raises()** (3 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_unversioned_input_stamps_v1()** (3 connections) — `tests/config/test_loader.py`
- **.test_latest_version_at_least_1()** (2 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_does_not_mutate_input()** (2 connections) — `tests/config/test_loader.py`
- **.test_run_migrations_rejects_downgrade()** (2 connections) — `tests/config/test_loader.py`
- **Migration** (1 connections)
- **Hal0Error** (1 connections)
- **hal0.config.migrations — versioned config migration transforms.  # TIER3: config** (1 connections) — `src/hal0/config/migrations/__init__.py`
- **# NOTE: downgrade migrations are explicitly unsupported in v1.** (1 connections) — `src/hal0/config/migrations/__init__.py`
- **Read ``meta.schema_version`` from a raw config dict, defaulting to 1.** (1 connections) — `src/hal0/config/migrations/__init__.py`
- **Cheap deepcopy for the TOML-shaped dicts we deal with.      TOML decodes to str** (1 connections) — `src/hal0/config/migrations/__init__.py`
- **Raised when a migration chain cannot be applied.** (1 connections) — `src/hal0/config/migrations/__init__.py`
- **Decorator that registers a migration as producing ``target_version``.** (1 connections) — `src/hal0/config/migrations/__init__.py`
- *... and 6 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (4 shared connections)
- [load_hal0_config](load_hal0_config.md) (4 shared connections)
- [config_commands.py](config_commands.py.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [test_config_migrate.py](test_config_migrate.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/migrations/__init__.py`
- `src/hal0/updater/updater.py`
- `tests/config/test_loader.py`

## Audit Trail

- EXTRACTED: 71 (70%)
- INFERRED: 31 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*