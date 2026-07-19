# test_hermes_upgrade.py

> 22 nodes

## Key Concepts

- **test_hermes_upgrade.py** (13 connections) — `tests/agents/test_hermes_upgrade.py`
- **upgrade_hermes_runtime()** (12 connections) — `src/hal0/agents/hermes_provision.py`
- **FakeRunner** (8 connections) — `tests/agents/test_hermes_upgrade.py`
- **_fake_venv()** (7 connections) — `tests/agents/test_hermes_upgrade.py`
- **test_happy_path_upgrades_then_migrates()** (7 connections) — `tests/agents/test_hermes_upgrade.py`
- **test_pip_failure_is_a_real_stop()** (7 connections) — `tests/agents/test_hermes_upgrade.py`
- **Path** (6 connections)
- **test_version_pin_installs_exact_spec()** (6 connections) — `tests/agents/test_hermes_upgrade.py`
- **test_migrate_failure_is_non_fatal()** (6 connections) — `tests/agents/test_hermes_upgrade.py`
- **test_missing_venv_is_actionable_stop()** (4 connections) — `tests/agents/test_hermes_upgrade.py`
- **_pip_call()** (3 connections) — `tests/agents/test_hermes_upgrade.py`
- **_migrate_call()** (3 connections) — `tests/agents/test_hermes_upgrade.py`
- **.__init__()** (2 connections) — `tests/agents/test_hermes_upgrade.py`
- **Any** (2 connections)
- **.run()** (2 connections) — `tests/agents/test_hermes_upgrade.py`
- **_is_pip()** (2 connections) — `tests/agents/test_hermes_upgrade.py`
- **_is_migrate()** (2 connections) — `tests/agents/test_hermes_upgrade.py`
- **test_requirements_floor_not_hard_pinned()** (2 connections) — `tests/agents/test_hermes_upgrade.py`
- **Pull the latest matching ``hermes-agent`` into the venv + reconcile config.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Tests for hal0.agents.hermes_provision.upgrade_hermes_runtime.  The runtime half** (1 connections) — `tests/agents/test_hermes_upgrade.py`
- **A venv whose bin/python exists so the missing-venv guard passes.** (1 connections) — `tests/agents/test_hermes_upgrade.py`
- **No accidental update-blocker: a commit-pin is allowed ONLY when it is the     re** (1 connections) — `tests/agents/test_hermes_upgrade.py`

## Relationships

- [Path](Path.md) (3 shared connections)
- [hermes_provision.py](hermes_provision.py.md) (1 shared connections)
- [Any](Any.md) (1 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`
- `tests/agents/test_hermes_upgrade.py`

## Audit Trail

- EXTRACTED: 83 (85%)
- INFERRED: 15 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*