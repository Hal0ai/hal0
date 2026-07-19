# memory_bank_commands.py

> 24 nodes · cohesion 0.17

## Key Concepts

- **memory_bank_commands.py** (13 connections) — `src/hal0/cli/memory_bank_commands.py`
- **_require_api()** (11 connections) — `src/hal0/cli/memory_bank_commands.py`
- **_emit()** (10 connections) — `src/hal0/cli/memory_bank_commands.py`
- **profile_set_cmd()** (9 connections) — `src/hal0/cli/memory_bank_commands.py`
- **.render()** (8 connections) — `src/hal0/providers/base.py`
- **bank_consolidate_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **bank_delete_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **bank_import_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **bank_list_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **bank_stats_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **profile_get_cmd()** (7 connections) — `src/hal0/cli/memory_bank_commands.py`
- **bank_export_cmd()** (5 connections) — `src/hal0/cli/memory_bank_commands.py`
- **_render_profile()** (4 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Any** (2 connections)
- **``hal0 memory bank`` — Hindsight bank admin CLI.  Thin HTTP client over the allo** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Show detailed stats for a single bank (nodes, links, operations, ...).** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Show a bank's profile (name, mission, disposition traits).** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Read-modify-write a bank's profile.      Disposition traits (--skepticism/--lite** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Export a bank as a portable template manifest (synchronous, no polling needed).** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Import a bank template manifest (synchronous, no polling needed).** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Irreversibly delete a bank (drops every memory/document/entity in it).      Requ** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Trigger consolidation for a bank (async — returns an operation id).** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **List banks with fact counts and last-activity timestamps.** (1 connections) — `src/hal0/cli/memory_bank_commands.py`
- **Return the ``{src}:{dst}[:ro[,z]]`` value for ``--volume=``.** (1 connections) — `src/hal0/providers/base.py`

## Relationships

- [die](die.md) (18 shared connections)
- [_shared.py](_shared.py.md) (2 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `src/hal0/cli/memory_bank_commands.py`
- `src/hal0/providers/base.py`

## Audit Trail

- EXTRACTED: 82 (72%)
- INFERRED: 32 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*