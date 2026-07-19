# build_auto_selections

> 49 nodes · cohesion 0.07

## Key Concepts

- **build_auto_selections()** (18 connections) — `src/hal0/cli/setup_command.py`
- **setup_command.py** (13 connections) — `src/hal0/cli/setup_command.py`
- **run_interactive()** (13 connections) — `src/hal0/cli/setup_command.py`
- **setup()** (11 connections) — `src/hal0/cli/setup_command.py`
- **_hw()** (11 connections) — `tests/cli/test_setup_command.py`
- **test_setup_command.py** (10 connections) — `tests/cli/test_setup_command.py`
- **setup_plan.py** (8 connections) — `src/hal0/cli/setup_plan.py`
- **resolve_plan_selections()** (8 connections) — `src/hal0/cli/setup_plan.py`
- **_run_auto()** (7 connections) — `src/hal0/cli/setup_command.py`
- **run_plan()** (7 connections) — `src/hal0/cli/setup_plan.py`
- **npu_healthy()** (7 connections) — `src/hal0/install/profile_derive.py`
- **_render_plan_table()** (6 connections) — `src/hal0/cli/setup_plan.py`
- **_existing_slot_names()** (5 connections) — `src/hal0/cli/setup_command.py`
- **_build_offline_deps()** (4 connections) — `src/hal0/cli/setup_command.py`
- **_confirm()** (4 connections) — `src/hal0/cli/setup_command.py`
- **_prompt()** (4 connections) — `src/hal0/cli/setup_command.py`
- **_validate_store()** (4 connections) — `src/hal0/cli/setup_command.py`
- **test_auto_selections_brain_scaffold_is_chat_capability()** (4 connections) — `tests/cli/test_setup_command.py`
- **Console** (3 connections)
- **_answer_file_strict()** (3 connections) — `src/hal0/cli/setup_plan.py`
- **_free_space_gib()** (3 connections) — `src/hal0/cli/setup_plan.py`
- **_port_in_use()** (3 connections) — `src/hal0/cli/setup_plan.py`
- **test_auto_selections_default_keeps_extensions_and_agent_slot()** (3 connections) — `tests/cli/test_setup_command.py`
- **test_auto_selections_no_existing_seeds_all_default()** (3 connections) — `tests/cli/test_setup_command.py`
- **test_auto_selections_no_extensions_disables_all_and_skips_agent_slot()** (3 connections) — `tests/cli/test_setup_command.py`
- *... and 24 more nodes in this community*

## Relationships

- [HardwareInfo](HardwareInfo.md) (8 shared connections)
- [load_answers](load_answers.md) (5 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (4 shared connections)
- [test_setup_install.py](test_setup_install.py.md) (2 shared connections)
- [install_openwebui](install_openwebui.md) (2 shared connections)
- [test_emit_answers.py](test_emit_answers.py.md) (2 shared connections)
- [test_probe.py](test_probe.py.md) (2 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (2 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [EventBus](EventBus.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [Check](Check.md) (1 shared connections)

## Source Files

- `src/hal0/cli/setup_command.py`
- `src/hal0/cli/setup_plan.py`
- `src/hal0/install/profile_derive.py`
- `tests/cli/test_setup_command.py`
- `tests/install/test_profile_derive.py`

## Audit Trail

- EXTRACTED: 154 (78%)
- INFERRED: 43 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*