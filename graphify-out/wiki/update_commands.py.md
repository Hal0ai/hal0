# update_commands.py

> 32 nodes · cohesion 0.09

## Key Concepts

- **update_commands.py** (18 connections) — `src/hal0/cli/update_commands.py`
- **update()** (18 connections) — `src/hal0/cli/update_commands.py`
- **update_owui()** (8 connections) — `src/hal0/cli/update_commands.py`
- **_refuse_if_editable()** (6 connections) — `src/hal0/cli/update_commands.py`
- **_restart_drifted_slots()** (6 connections) — `src/hal0/cli/update_commands.py`
- **_fetch_slot_drift()** (5 connections) — `src/hal0/cli/update_commands.py`
- **_poll_job()** (5 connections) — `src/hal0/cli/update_commands.py`
- **_interactive()** (4 connections) — `src/hal0/cli/update_commands.py`
- **UpdateChannel** (4 connections) — `src/hal0/cli/update_commands.py`
- **_write_unit_atomic()** (4 connections) — `src/hal0/cli/update_commands.py`
- **_dev_mode()** (3 connections) — `src/hal0/cli/update_commands.py`
- **_print_check()** (3 connections) — `src/hal0/cli/update_commands.py`
- **_print_drift_banner()** (3 connections) — `src/hal0/cli/update_commands.py`
- **_render_notes()** (3 connections) — `src/hal0/cli/update_commands.py`
- **_run_cmd()** (3 connections) — `src/hal0/cli/update_commands.py`
- **Context** (1 connections)
- **Path** (1 connections)
- **StrEnum** (1 connections)
- **CLI implementation for ``hal0 update``.  Thin client over the /api/updates/* sur** (1 connections) — `src/hal0/cli/update_commands.py`
- **Poll /api/updates/status/<id> until it reaches a terminal state.** (1 connections) — `src/hal0/cli/update_commands.py`
- **True on an interactive TTY — the gate for the pre-commit confirm prompt.      Fa** (1 connections) — `src/hal0/cli/update_commands.py`
- **Render the release notes from a prepared update job.      ``breaking`` / ``migra** (1 connections) — `src/hal0/cli/update_commands.py`
- **Return the /api/updates/slot-drift payload, or an empty summary on error.      B** (1 connections) — `src/hal0/cli/update_commands.py`
- **Post-update ``N slots need restart`` banner (or a clean all-good line).      ``r** (1 connections) — `src/hal0/cli/update_commands.py`
- **POST /api/updates/restart-slots and report the outcome.      Clean-path message** (1 connections) — `src/hal0/cli/update_commands.py`
- *... and 7 more nodes in this community*

## Relationships

- [die](die.md) (13 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (2 shared connections)
- [ConfigParseError](ConfigParseError.md) (2 shared connections)
- [Enum](Enum.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/update_commands.py`

## Audit Trail

- EXTRACTED: 94 (85%)
- INFERRED: 16 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*