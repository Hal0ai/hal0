# config_commands.py

> 16 nodes · cohesion 0.19

## Key Concepts

- **config_commands.py** (12 connections) — `src/hal0/cli/config_commands.py`
- **config_migrate()** (7 connections) — `src/hal0/cli/config_commands.py`
- **_config_path()** (7 connections) — `src/hal0/cli/config_commands.py`
- **ConfigFile** (7 connections) — `src/hal0/cli/config_commands.py`
- **config_edit()** (5 connections) — `src/hal0/cli/config_commands.py`
- **_hal0_toml_path()** (5 connections) — `src/hal0/cli/config_commands.py`
- **config_show()** (4 connections) — `src/hal0/cli/config_commands.py`
- **Path** (2 connections)
- **StrEnum** (1 connections)
- **hal0 config subcommands — thin HTTP client to the hal0 API.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Open a hal0 config file in $EDITOR (default: hal0.toml; falls back to $VISUAL th** (1 connections) — `src/hal0/cli/config_commands.py`
- **Migrate hal0.toml forward to the latest config schema version.      Reads ``meta** (1 connections) — `src/hal0/cli/config_commands.py`
- **Which on-disk config file a `config show`/`config edit` targets.      Mirrors th** (1 connections) — `src/hal0/cli/config_commands.py`
- **Return the on-disk path for one of hal0's config files, honouring HAL0_HOME.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Return the on-disk hal0.toml path, honouring HAL0_HOME.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Print a hal0 config file as it exists on disk (default: hal0.toml).** (1 connections) — `src/hal0/cli/config_commands.py`

## Relationships

- [die](die.md) (4 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [run_migrations](run_migrations.md) (2 shared connections)
- [Enum](Enum.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/config_commands.py`

## Audit Trail

- EXTRACTED: 51 (89%)
- INFERRED: 6 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*