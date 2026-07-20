# _hermes_fakes.py

> 18 nodes · cohesion 0.14

## Key Concepts

- **_hermes_fakes.py** (12 connections) — `tests/agents/_hermes_fakes.py`
- **_FakeSystemctl** (5 connections) — `tests/agents/test_hermes_provision.py`
- **apply_hermes_config_cli()** (4 connections) — `tests/agents/_hermes_fakes.py`
- **_coerce()** (4 connections) — `tests/agents/_hermes_fakes.py`
- **default_fake_slots()** (4 connections) — `tests/agents/_hermes_fakes.py`
- **Any** (3 connections)
- **_set_dotted()** (3 connections) — `tests/agents/_hermes_fakes.py`
- **.run()** (3 connections) — `tests/agents/test_hermes_provision.py`
- **apply_kanban_db_init_cli()** (2 connections) — `tests/agents/_hermes_fakes.py`
- **_Completed** (2 connections) — `tests/agents/_hermes_fakes.py`
- **Any** (2 connections)
- **Shared test fakes for the hermes_provision config-set redesign.  ``apply_hermes_** (1 connections) — `tests/agents/_hermes_fakes.py`
- **Ready chat/agent/utility slots + an embed slot that must never alias.** (1 connections) — `tests/agents/_hermes_fakes.py`
- **Mirror ``hermes config set`` value coercion (verified on 0.17).** (1 connections) — `tests/agents/_hermes_fakes.py`
- **Apply a ``hermes config set/migrate`` argv to ``$HERMES_HOME/config.yaml``.** (1 connections) — `tests/agents/_hermes_fakes.py`
- **Simulate ``hermes_cli.kanban_db.init_db(<path>)`` by creating its tables.      D** (1 connections) — `tests/agents/_hermes_fakes.py`
- **.__init__()** (1 connections) — `tests/agents/test_hermes_provision.py`
- **Capture subprocess.run argv so tests can assert daemon-reload calls.** (1 connections) — `tests/agents/test_hermes_provision.py`

## Relationships

- [test_hermes_provision.py](test_hermes_provision.py.md) (5 shared connections)
- [test_hermes_provision_idempotency.py](test_hermes_provision_idempotency.py.md) (4 shared connections)

## Source Files

- `tests/agents/_hermes_fakes.py`
- `tests/agents/test_hermes_provision.py`

## Audit Trail

- EXTRACTED: 51 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*