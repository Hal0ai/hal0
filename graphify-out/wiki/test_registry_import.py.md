# test_registry_import.py

> 64 nodes · cohesion 0.07

## Key Concepts

- **test_registry_import.py** (28 connections) — `tests/cli/test_registry_import.py`
- **Path** (23 connections)
- **_invoke()** (17 connections) — `tests/cli/test_registry_import.py`
- **registry_commands.py** (12 connections) — `src/hal0/cli/registry_commands.py`
- **_make_backup()** (12 connections) — `tests/cli/test_registry_import.py`
- **_do_import_backup()** (8 connections) — `src/hal0/cli/registry_commands.py`
- **Path** (8 connections)
- **_atomic_copy()** (7 connections) — `src/hal0/cli/registry_commands.py`
- **_is_within()** (7 connections) — `src/hal0/cli/registry_commands.py`
- **export_registry()** (6 connections) — `src/hal0/cli/registry_commands.py`
- **_find_registry_in_dir()** (6 connections) — `src/hal0/cli/registry_commands.py`
- **_safe_extract()** (6 connections) — `src/hal0/cli/registry_commands.py`
- **TarFile** (6 connections)
- **test_model_import_backup.py** (6 connections) — `tests/cli/test_model_import_backup.py`
- **model_import_backup()** (4 connections) — `src/hal0/cli/model_commands.py`
- **import_backup()** (4 connections) — `src/hal0/cli/registry_commands.py`
- **import_sqlite()** (4 connections) — `src/hal0/cli/registry_commands.py`
- **_make_backup()** (4 connections) — `tests/cli/test_model_import_backup.py`
- **test_import_accepts_flat_layout()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_accepts_short_layout()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_cleans_tempdir_on_success()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_force_overwrites_existing()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_handles_permission_error_on_dest()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_happy_path_canonical_layout()** (4 connections) — `tests/cli/test_registry_import.py`
- **test_import_ignores_extras_in_backup()** (4 connections) — `tests/cli/test_registry_import.py`
- *... and 39 more nodes in this community*

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (3 shared connections)
- [model_commands.py](model_commands.py.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (1 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [test_updater.py](test_updater.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/model_commands.py`
- `src/hal0/cli/registry_commands.py`
- `tests/cli/test_model_import_backup.py`
- `tests/cli/test_registry_import.py`

## Audit Trail

- EXTRACTED: 254 (92%)
- INFERRED: 21 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*