# app_commands.py

> 10 nodes · cohesion 0.22

## Key Concepts

- **app_commands.py** (6 connections) — `src/hal0/cli/app_commands.py`
- **app_install()** (4 connections) — `src/hal0/cli/app_commands.py`
- **app_list()** (3 connections) — `src/hal0/cli/app_commands.py`
- **app_uninstall()** (3 connections) — `src/hal0/cli/app_commands.py`
- **_systemctl_query()** (3 connections) — `src/hal0/cli/app_commands.py`
- **``hal0 app`` subcommands — deferred install/uninstall verbs for optional apps.** (1 connections) — `src/hal0/cli/app_commands.py`
- **List known apps and their systemd enabled/active state.** (1 connections) — `src/hal0/cli/app_commands.py`
- **Stop + disable an app installed via `hal0 app install`.      Only tears down the** (1 connections) — `src/hal0/cli/app_commands.py`
- **Install + enable an app that was skipped at install time.      Runs the identica** (1 connections) — `src/hal0/cli/app_commands.py`
- **Best-effort ``systemctl is-<prop> <unit>`` — returns the raw stdout     (e.g. 'a** (1 connections) — `src/hal0/cli/app_commands.py`

## Relationships

- [die](die.md) (2 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [install_openwebui](install_openwebui.md) (1 shared connections)

## Source Files

- `src/hal0/cli/app_commands.py`

## Audit Trail

- EXTRACTED: 21 (88%)
- INFERRED: 3 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*