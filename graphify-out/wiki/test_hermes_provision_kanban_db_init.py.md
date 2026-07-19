# test_hermes_provision_kanban_db_init.py

> 15 nodes

## Key Concepts

- **test_hermes_provision_kanban_db_init.py** (9 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **Path** (6 connections)
- **test_reinvokes_when_some_tables_missing()** (5 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **kanban_state()** (4 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **_seed_tables()** (4 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **test_skips_when_hermes_venv_absent()** (4 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **test_converges_when_tables_already_present()** (4 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **test_invokes_hermes_init_db_when_tables_missing()** (3 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **test_init_db_failure_surfaces_as_fail()** (3 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **test_kanban_db_init_runs_after_install_and_home_init()** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **``kanban_db_init`` phase contract (O20).  The Hermes gateway's kanban-board WATC** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **A BootstrapState with a real (but empty) venv/bin/python stub.** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **A fresh/partial install with no venv yet must SKIP, not FAIL.** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **A genuine subprocess error (bad venv, crash) fails the step — this is     NOT a** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`
- **A partially-initialized DB (e.g. an interrupted prior run) still counts     as n** (1 connections) — `tests/agents/test_hermes_provision_kanban_db_init.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (6 shared connections)

## Source Files

- `tests/agents/test_hermes_provision_kanban_db_init.py`

## Audit Trail

- EXTRACTED: 48 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*