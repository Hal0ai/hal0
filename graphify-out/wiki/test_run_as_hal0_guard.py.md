# test_run_as_hal0_guard.py

> 24 nodes

## Key Concepts

- **test_run_as_hal0_guard.py** (12 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **_run_guard()** (8 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_root_reexecs_as_target_user_via_runuser()** (6 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_reexec_actually_runs_with_clean_env()** (6 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **Path** (5 connections)
- **_write_stub()** (5 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **_existing_unprivileged_user()** (4 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_root_without_any_dropper_refuses()** (4 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_non_root_returns_without_running_command()** (3 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_root_with_opt_out_does_not_reexec()** (3 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_install_sh_installs_guard_into_lib_dir()** (2 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **test_hermes_wrapper_sources_guard_and_calls_it_first()** (2 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **CompletedProcess** (1 connections)
- **test_guard_file_exists()** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **Tests for the shared run-as-hal0 privilege-drop guard.  ``installer/lib/run-as-h** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **A user that exists on the test host but isn't us — 'nobody' on Linux.** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **Source the guard and run ``script`` under /bin/sh, capturing output.** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **A non-root caller proceeds with its own perms — the guard must NOT     run (or e** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **HAL0_ALLOW_ROOT=1 lets a deliberate root session through unchanged.** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **As root, the guard re-execs the command as the target user with HOME     set and** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **Execute the re-exec for real (stub runuser EXECS the wrapped command) so     `en** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **install.sh must drop the guard lib at the absolute path the wrapper     sources** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **The hermes wrapper must source the guard and invoke it before doing any     real** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`
- **If no runuser/setpriv/sudo is available, the guard refuses (non-zero)     rather** (1 connections) — `tests/agents/test_run_as_hal0_guard.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/agents/test_run_as_hal0_guard.py`

## Audit Trail

- EXTRACTED: 72 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*