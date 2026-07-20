# CliRunner

> 20 nodes · cohesion 0.18

## Key Concepts

- **CliRunner** (10 connections)
- **test_agents_personas.py** (10 connections) — `tests/cli/test_agents_personas.py`
- **Path** (8 connections)
- **test_personas_activate_failed_reload_warns_but_succeeds()** (5 connections) — `tests/cli/test_agents_personas.py`
- **isolated_personas()** (4 connections) — `tests/cli/test_agents_personas.py`
- **test_personas_activate_writes_pointer_and_emits_status()** (4 connections) — `tests/cli/test_agents_personas.py`
- **test_install_hermes_no_longer_accepts_adopt()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **MonkeyPatch** (3 connections)
- **test_personas_activate_missing_persona_exits_nonzero()** (3 connections) — `tests/cli/test_agents_personas.py`
- **test_personas_list_after_seed()** (3 connections) — `tests/cli/test_agents_personas.py`
- **test_personas_list_empty_emits_install_hint()** (3 connections) — `tests/cli/test_agents_personas.py`
- **test_personas_show_emits_toml()** (3 connections) — `tests/cli/test_agents_personas.py`
- **test_personas_show_missing_persona_exits_nonzero()** (3 connections) — `tests/cli/test_agents_personas.py`
- **test_headless_interactive_prints_stage2_command()** (3 connections) — `tests/cli/test_setup_command.py`
- **cli_runner()** (2 connections) — `tests/cli/test_agents_personas.py`
- **O14: `--adopt` is spec-retired — the CLI parser must reject the flag.      The s** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **CLI tests for ``hal0 agent personas {list,show,activate}`` (PR-3, v0.3).  These** (1 connections) — `tests/cli/test_agents_personas.py`
- **Redirect the personas module to a per-test root.** (1 connections) — `tests/cli/test_agents_personas.py`
- **If Hermes isn't running the nudge fails but the activation     succeeds — the fi** (1 connections) — `tests/cli/test_agents_personas.py`
- **Two-stage handoff (issue #1112): a piped / non-TTY `hal0 setup` (no     --auto)** (1 connections) — `tests/cli/test_setup_command.py`

## Relationships

- [test_agent_install_hermes.py](test_agent_install_hermes.py.md) (1 shared connections)
- [build_auto_selections](build_auto_selections.md) (1 shared connections)

## Source Files

- `tests/cli/test_agent_install_hermes.py`
- `tests/cli/test_agents_personas.py`
- `tests/cli/test_setup_command.py`

## Audit Trail

- EXTRACTED: 68 (94%)
- INFERRED: 4 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*