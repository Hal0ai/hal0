# settings.py

> 35 nodes · cohesion 0.11

## Key Concepts

- **settings.py** (15 connections) — `src/hal0/api/routes/settings.py`
- **_apply_store_change()** (15 connections) — `src/hal0/api/routes/settings.py`
- **update_settings()** (13 connections) — `src/hal0/api/routes/settings.py`
- **Any** (12 connections)
- **_config_to_dict()** (9 connections) — `src/hal0/api/routes/settings.py`
- **set_model_store()** (9 connections) — `src/hal0/api/routes/settings.py`
- **_store_state_payload()** (9 connections) — `src/hal0/api/routes/settings.py`
- **Request** (7 connections)
- **ConfigInvalidError** (6 connections) — `src/hal0/api/routes/settings.py`
- **get_model_store()** (6 connections) — `src/hal0/api/routes/settings.py`
- **get_settings()** (6 connections) — `src/hal0/api/routes/settings.py`
- **migrate_model_store()** (6 connections) — `src/hal0/api/routes/settings.py`
- **reload_settings()** (6 connections) — `src/hal0/api/routes/settings.py`
- **_validation_error_details()** (5 connections) — `src/hal0/api/routes/settings.py`
- **_deep_merge()** (4 connections) — `src/hal0/api/routes/settings.py`
- **settings_schema()** (4 connections) — `src/hal0/api/routes/settings.py`
- **get_apply_plan()** (3 connections) — `src/hal0/api/routes/settings.py`
- **Hal0Error** (3 connections)
- **MigrationPlan** (1 connections)
- **ValidationError** (1 connections)
- **Settings (config) endpoints (mounted under /api/settings).  Typed read/write of** (1 connections) — `src/hal0/api/routes/settings.py`
- **Return the current Hal0Config as JSON.      The dashboard's Settings view reads** (1 connections) — `src/hal0/api/routes/settings.py`
- **Apply a partial update to hal0.toml.      Body shape: any subset of ``Hal0Config** (1 connections) — `src/hal0/api/routes/settings.py`
- **Re-read hal0.toml from disk into ``app.state.hal0_config``.      Returns the fre** (1 connections) — `src/hal0/api/routes/settings.py`
- **Return the pydantic JSON schema of Hal0Config.      Lets the dashboard render fi** (1 connections) — `src/hal0/api/routes/settings.py`
- *... and 10 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (12 shared connections)
- [test_model_store.py](test_model_store.py.md) (4 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)
- [test_redact.py](test_redact.py.md) (1 shared connections)
- [apply_plan](apply_plan.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/settings.py`

## Audit Trail

- EXTRACTED: 139 (90%)
- INFERRED: 16 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*