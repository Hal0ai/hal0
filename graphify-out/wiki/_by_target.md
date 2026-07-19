# _by_target

> 13 nodes · cohesion 0.15

## Key Concepts

- **_by_target()** (9 connections) — `tests/install/test_perms.py`
- **test_flip_honors_custom_service_group()** (3 connections) — `tests/install/test_perms.py`
- **test_flip_keeps_agents_and_secrets_root_owned()** (3 connections) — `tests/install/test_perms.py`
- **test_flip_makes_etc_hal0_service_owned_and_setgid()** (3 connections) — `tests/install/test_perms.py`
- **test_flip_makes_state_root_service_owned()** (3 connections) — `tests/install/test_perms.py`
- **test_root_table_is_unchanged_by_the_flip()** (3 connections) — `tests/install/test_perms.py`
- **test_runtime_slots_and_registry_are_service_owned()** (3 connections) — `tests/install/test_perms.py`
- **service_user="root" must reproduce the byte-identical root-era table.      Exist** (1 connections) — `tests/install/test_perms.py`
- **service_user="hal0" hands /etc/hal0 + its mutable contents to the daemon.      T** (1 connections) — `tests/install/test_perms.py`
- **agents/ + secrets/ must stay root:root even under the flip.      The API only re** (1 connections) — `tests/install/test_perms.py`
- **/var/lib/hal0 + HERMES_HOME flip to the service user under the flip.** (1 connections) — `tests/install/test_perms.py`
- **O13: the /var/lib/hal0 runtime slots/ + registry/ trees must be declared.      i** (1 connections) — `tests/install/test_perms.py`
- **A non-default service_group threads through the service-owned rows.** (1 connections) — `tests/install/test_perms.py`

## Relationships

- [test_perms.py](test_perms.py.md) (8 shared connections)
- [PermRow](PermRow.md) (1 shared connections)

## Source Files

- `tests/install/test_perms.py`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*