# Enum

> 31 nodes

## Key Concepts

- **Enum** (13 connections)
- **config_commands.py** (12 connections) — `src/hal0/cli/config_commands.py`
- **exposure.py** (9 connections) — `src/hal0/security/exposure.py`
- **ConfigFile** (7 connections) — `src/hal0/cli/config_commands.py`
- **_config_path()** (7 connections) — `src/hal0/cli/config_commands.py`
- **AuthClass** (7 connections) — `src/hal0/security/exposure.py`
- **match_rule()** (7 connections) — `src/hal0/security/exposure.py`
- **_hal0_toml_path()** (5 connections) — `src/hal0/cli/config_commands.py`
- **config_edit()** (5 connections) — `src/hal0/cli/config_commands.py`
- **config_show()** (4 connections) — `src/hal0/cli/config_commands.py`
- **classify()** (4 connections) — `src/hal0/security/exposure.py`
- **_exact()** (3 connections) — `src/hal0/security/exposure.py`
- **_prefix()** (3 connections) — `src/hal0/security/exposure.py`
- **_Rule** (3 connections) — `src/hal0/security/exposure.py`
- **Path** (2 connections)
- **Matcher** (2 connections)
- **_outside_api_v1_mcp()** (2 connections) — `src/hal0/security/exposure.py`
- **.applies()** (2 connections) — `src/hal0/security/exposure.py`
- **hal0 config subcommands — thin HTTP client to the hal0 API.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Which on-disk config file a `config show`/`config edit` targets.      Mirrors th** (1 connections) — `src/hal0/cli/config_commands.py`
- **Return the on-disk path for one of hal0's config files, honouring HAL0_HOME.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Return the on-disk hal0.toml path, honouring HAL0_HOME.** (1 connections) — `src/hal0/cli/config_commands.py`
- **Print a hal0 config file as it exists on disk (default: hal0.toml).** (1 connections) — `src/hal0/cli/config_commands.py`
- **Open a hal0 config file in $EDITOR (default: hal0.toml; falls back to $VISUAL th** (1 connections) — `src/hal0/cli/config_commands.py`
- **Route -> :class:`AuthClass` classification table (KB-1 / §1, seam S9).  Single s** (1 connections) — `src/hal0/security/exposure.py`
- *... and 6 more nodes in this community*

## Relationships

- [die](die.md) (5 shared connections)
- [auth.py](auth.py.md) (3 shared connections)
- [run_migrations](run_migrations.md) (2 shared connections)
- [test_exposure.py](test_exposure.py.md) (2 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)
- [hermes_provision.py](hermes_provision.py.md) (1 shared connections)
- [runner.py](runner.py.md) (1 shared connections)
- [update_commands.py](update_commands.py.md) (1 shared connections)
- [pve.py](pve.py.md) (1 shared connections)
- [MemoryProvider](MemoryProvider.md) (1 shared connections)

## Source Files

- `src/hal0/cli/config_commands.py`
- `src/hal0/security/exposure.py`

## Audit Trail

- EXTRACTED: 104 (95%)
- INFERRED: 6 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*