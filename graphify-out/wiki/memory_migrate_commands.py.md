# memory_migrate_commands.py

> 22 nodes

## Key Concepts

- **memory_migrate_commands.py** (12 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **migrate_unify_cmd()** (12 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **migrate_default()** (8 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **sync_graph_cmd()** (6 connections) — `src/hal0/cli/memory_commands.py`
- **_load_honcho_cli_config()** (6 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_run_migrate_honcho_to_hindsight()** (6 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **honcho_render_env_cmd()** (5 connections) — `src/hal0/cli/memory_commands.py`
- **_migrate_state()** (5 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_run_migrate_hindsight_to_honcho()** (5 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Any** (4 connections)
- **_document_ids()** (4 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_poll_operation()** (3 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_retag_documents()** (3 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **_derived_tags()** (2 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Sync Honcho conclusions → Hindsight (resumes from the saved watermark).      Equ** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Render ``/etc/hal0/honcho.env`` from ``hal0.toml [honcho]`` and (best-effort) re** (1 connections) — `src/hal0/cli/memory_commands.py`
- **Context** (1 connections)
- **``hal0 memory migrate`` — Hindsight<->Honcho engine migration + bank unify.  The** (1 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Migrate memory between engines (Hindsight<->Honcho).      ``--from hindsight --t** (1 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Best-effort set of document ids currently in ``bank``.      ``GET .../documents`** (1 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Read-merge-write ``tags`` onto each of ``doc_ids`` in ``bank``.      PATCH .../d** (1 connections) — `src/hal0/cli/memory_migrate_commands.py`
- **Fold one or more source banks into a target bank, tagging provenance.      Never** (1 connections) — `src/hal0/cli/memory_migrate_commands.py`

## Relationships

- [die](die.md) (12 shared connections)
- [_shared.py](_shared.py.md) (3 shared connections)
- [MigrateState](MigrateState.md) (3 shared connections)
- [HonchoConfig](HonchoConfig.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `src/hal0/cli/memory_commands.py`
- `src/hal0/cli/memory_migrate_commands.py`

## Audit Trail

- EXTRACTED: 63 (71%)
- INFERRED: 26 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*