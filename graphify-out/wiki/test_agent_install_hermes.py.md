# test_agent_install_hermes.py

> 34 nodes

## Key Concepts

- **test_agent_install_hermes.py** (29 connections) — `tests/cli/test_agent_install_hermes.py`
- **_Rec** (7 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_aborts_when_provisioning_fails()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_runs_gateway_by_default()** (3 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_runs_prereqs_then_bootstrap_then_register()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_guard_noop_when_root()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_guard_noop_when_writable()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_no_gateway_flag_skips_it()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_gateway_noop_when_venv_missing()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_gateway_installs_and_enables_unit()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_gateway_writes_dropin_before_gateway_install()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_gateway_skips_enable_on_foreign_gateway()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_gateway_warns_without_raising_when_unit_missing()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_provision_hermes_non_root_runs_in_process()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_provision_hermes_root_drops_to_hal0()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_run_as_hal0_builds_runuser_argv()** (2 connections) — `tests/cli/test_agent_install_hermes.py`
- **.__init__()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enable_and_start_unit_invokes_systemctl_when_present()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_enable_and_start_unit_noops_without_systemd()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **test_install_hermes_guard_aborts_non_root_when_unwritable()** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **`hal0 agent install hermes` foreground-provision flow.  Regression for the clean** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **Records calls in order so the test can assert sequencing.** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **A non-zero bootstrap rc must stop the flow before the API register —     we don'** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **Root writes anywhere — the guard must not even probe the filesystem.** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- **Dev / rootless install already owns the trees — proceed silently.** (1 connections) — `tests/cli/test_agent_install_hermes.py`
- *... and 9 more nodes in this community*

## Relationships

- [_FakeAgentManager](_FakeAgentManager.md) (7 shared connections)
- [_fake_bundled_agent_manager](_fake_bundled_agent_manager.md) (2 shared connections)
- [CliRunner](CliRunner.md) (1 shared connections)

## Source Files

- `tests/cli/test_agent_install_hermes.py`

## Audit Trail

- EXTRACTED: 84 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*