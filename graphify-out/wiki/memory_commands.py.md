# memory_commands.py

> 31 nodes · cohesion 0.10

## Key Concepts

- **memory_commands.py** (14 connections) — `src/hal0/cli/memory_commands.py`
- **memory_migrate_commands.py** (12 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **migrate_unify_cmd()** (12 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **migrate_default()** (8 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_set_enabled()** (7 connections) — `src/hal0/cli/memory_commands.py`
- **sync_graph_cmd()** (6 connections) — `src/hal0/cli/memory_commands.py`
- **_load_honcho_cli_config()** (6 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_run_migrate_honcho_to_hindsight()** (6 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **honcho_render_env_cmd()** (5 connections) — `src/hal0/cli/memory_commands.py`
- **_migrate_state()** (5 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_run_migrate_hindsight_to_honcho()** (5 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_document_ids()** (4 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Any** (4 connections)
- **disable_cmd()** (3 connections) — `src/hal0/cli/memory_commands.py`
- **enable_cmd()** (3 connections) — `src/hal0/cli/memory_commands.py`
- **_render_provider_status()** (3 connections) — `src/hal0/cli/memory_commands.py`
- **_poll_operation()** (3 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_retag_documents()** (3 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_derived_tags()** (2 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Any** (1 connections)
- **hal0 memory subcommands — graph-extraction gate.  Mirrors the slot / model CLI s** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Enable the memory subsystem (persists [memory].enabled=true).** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Disable the memory subsystem (persists [memory].enabled=false).** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Sync Honcho conclusions → Hindsight (resumes from the saved watermark).      Equ** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Render ``/etc/hal0/honcho.env`` from ``hal0.toml [honcho]`` and (best-effort) re** (1 connections) — `src/hal0/cli/memory_commands.py`
- *... and 6 more nodes in this community*

## Relationships

- [die](die.md) (23 shared connections)
- [MigrateState](MigrateState.md) (3 shared connections)
- [Typer](Typer.md) (2 shared connections)
- [HonchoConfig](HonchoConfig.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/memory_commands.py`
- `src/hal0/cli/memory_migrate_commands.py`

## Audit Trail

- EXTRACTED: 93 (76%)
- INFERRED: 30 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*