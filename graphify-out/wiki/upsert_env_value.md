# upsert_env_value

> 15 nodes · cohesion 0.21

## Key Concepts

- **upsert_env_value()** (9 connections) — `src/hal0/api/_env_store.py`
- **_env_store.py** (7 connections) — `src/hal0/api/_env_store.py`
- **delete_env_value()** (6 connections) — `src/hal0/api/_env_store.py`
- **_atomic_write()** (5 connections) — `src/hal0/api/_env_store.py`
- **_line_targets_key()** (4 connections) — `src/hal0/api/_env_store.py`
- **list_env_keys()** (4 connections) — `src/hal0/api/_env_store.py`
- **Path** (4 connections)
- **_escape()** (3 connections) — `src/hal0/api/_env_store.py`
- **Atomic ``api.env`` secret store shared by the providers + secrets routers.  ``/e** (1 connections) — `src/hal0/api/_env_store.py`
- **Remove every line setting ``key`` from ``api_env`` atomically.      Returns ``Tr** (1 connections) — `src/hal0/api/_env_store.py`
- **Return the sorted, de-duplicated set of keys set in ``api_env``.      Only uncom** (1 connections) — `src/hal0/api/_env_store.py`
- **Escape a secret for a double-quoted ``EnvironmentFile`` value.      systemd trea** (1 connections) — `src/hal0/api/_env_store.py`
- **True when ``line`` sets ``key`` (plain or commented out).** (1 connections) — `src/hal0/api/_env_store.py`
- **Write ``text`` to ``api_env`` atomically with mode 0600.      tmp-file in the sa** (1 connections) — `src/hal0/api/_env_store.py`
- **Upsert ``key="<escaped-value>"`` in ``api_env`` atomically.      If a line for `** (1 connections) — `src/hal0/api/_env_store.py`

## Relationships

- [secrets.py](secrets.py.md) (2 shared connections)
- [list_secrets](list_secrets.md) (1 shared connections)
- [providers.py](providers.py.md) (1 shared connections)
- [test_secrets.py](test_secrets.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/_env_store.py`

## Audit Trail

- EXTRACTED: 44 (90%)
- INFERRED: 5 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*