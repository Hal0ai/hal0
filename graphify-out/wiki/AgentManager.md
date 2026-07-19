# AgentManager

> 74 nodes · cohesion 0.07

## Key Concepts

- **AgentManager** (54 connections) — `src/hal0/agents/manager.py`
- **test_manager.py** (39 connections) — `tests/agents/test_manager.py`
- **_StubDriver** (35 connections) — `tests/agents/test_manager.py`
- **.install()** (18 connections) — `tests/agents/test_manager.py`
- **Path** (14 connections)
- **.uninstall()** (12 connections) — `tests/agents/test_manager.py`
- **_seed_managed_home()** (9 connections) — `tests/agents/test_manager.py`
- **_seed_state_dir()** (9 connections) — `tests/agents/test_manager.py`
- **test_uninstall_reports_residual_on_permission_error()** (9 connections) — `tests/agents/test_manager.py`
- **test_hermes_uninstall_removes_managed_home()** (8 connections) — `tests/agents/test_manager.py`
- **test_install_uninstall_install_uninstall_round_trip()** (8 connections) — `tests/agents/test_manager.py`
- **test_uninstall_removes_state_dir()** (8 connections) — `tests/agents/test_manager.py`
- **test_uninstall_with_missing_seed_still_reports_uninstalled()** (8 connections) — `tests/agents/test_manager.py`
- **MonkeyPatch** (7 connections)
- **test_switch_aborts_without_uninstalling_when_target_script_missing()** (7 connections) — `tests/agents/test_manager.py`
- **test_hermes_install_records_hermes_home_as_data_dir()** (6 connections) — `tests/agents/test_manager.py`
- **test_hermes_uninstall_refuses_unmanaged_home()** (6 connections) — `tests/agents/test_manager.py`
- **test_uninstall_removes_seed_and_data_dir()** (6 connections) — `tests/agents/test_manager.py`
- **test_uninstall_unmanaged_home_is_left_intact_not_a_failure()** (6 connections) — `tests/agents/test_manager.py`
- **test_install_generic_agent_writes_seed_and_data_dir()** (5 connections) — `tests/agents/test_manager.py`
- **test_install_second_agent_without_switch_raises()** (5 connections) — `tests/agents/test_manager.py`
- **test_installed_names_includes_orphan_state_dir()** (5 connections) — `tests/agents/test_manager.py`
- **test_switch_failed_install_rolls_back_incumbent()** (5 connections) — `tests/agents/test_manager.py`
- **test_uninstall_with_no_artifacts_returns_false()** (5 connections) — `tests/agents/test_manager.py`
- **stub_drivers()** (4 connections) — `tests/agents/test_manager.py`
- *... and 49 more nodes in this community*

## Relationships

- [manager.py](manager.py.md) (22 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)
- [test_agent_uninstall_memory.py](test_agent_uninstall_memory.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/manager.py`
- `tests/agents/test_manager.py`

## Audit Trail

- EXTRACTED: 392 (98%)
- INFERRED: 9 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*