# test_migrate_model_layout.py

> 86 nodes · cohesion 0.06

## Key Concepts

- **test_migrate_model_layout.py** (41 connections) — `tests/cli/test_migrate_model_layout.py`
- **Path** (27 connections)
- **_make_tree()** (23 connections) — `tests/cli/test_migrate_model_layout.py`
- **_touch_file()** (21 connections) — `tests/cli/test_migrate_model_layout.py`
- **migrate_commands.py** (19 connections) — `src/hal0/cli/migrate_commands.py`
- **_write_registry()** (15 connections) — `tests/cli/test_migrate_model_layout.py`
- **_classify_registry_entry()** (14 connections) — `src/hal0/cli/migrate_commands.py`
- **plan_migration()** (13 connections) — `src/hal0/cli/migrate_commands.py`
- **Path** (11 connections)
- **_invoke()** (9 connections) — `tests/cli/test_migrate_model_layout.py`
- **model_layout()** (8 connections) — `src/hal0/cli/migrate_commands.py`
- **_pick_capability()** (8 connections) — `src/hal0/cli/migrate_commands.py`
- **execute_plan()** (7 connections) — `src/hal0/cli/migrate_commands.py`
- **test_execute_plan_crash_midway_leaves_no_broken_state()** (7 connections) — `tests/cli/test_migrate_model_layout.py`
- **_atomic_symlink()** (6 connections) — `src/hal0/cli/migrate_commands.py`
- **SymlinkAction** (6 connections) — `src/hal0/cli/migrate_commands.py`
- **test_cli_apply_creates_symlinks()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **test_cli_apply_is_idempotent()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **test_cli_dry_run_does_not_write()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **test_cli_force_overwrites_differing_symlink_in_apply()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **test_cli_refuses_overwrite_without_force_in_apply()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **test_plan_dedupes_registry_and_disk_scan()** (6 connections) — `tests/cli/test_migrate_model_layout.py`
- **_entry_disk_path()** (5 connections) — `src/hal0/cli/migrate_commands.py`
- **MigrationReport** (5 connections) — `src/hal0/cli/migrate_commands.py`
- **_plan_one_link()** (5 connections) — `src/hal0/cli/migrate_commands.py`
- *... and 61 more nodes in this community*

## Relationships

- [Typer](Typer.md) (1 shared connections)
- [test_doctor_models.py](test_doctor_models.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `src/hal0/cli/migrate_commands.py`
- `tests/cli/test_migrate_model_layout.py`

## Audit Trail

- EXTRACTED: 403 (92%)
- INFERRED: 36 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*