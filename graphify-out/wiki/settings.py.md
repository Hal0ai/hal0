# settings.py

> 34 nodes

## Key Concepts

- **settings.py** (15 connections) — `src/hal0/api/routes/settings.py`
- **_apply_store_change()** (15 connections) — `src/hal0/api/routes/settings.py`
- **update_settings()** (13 connections) — `src/hal0/api/routes/settings.py`
- **Any** (12 connections)
- **_config_to_dict()** (9 connections) — `src/hal0/api/routes/settings.py`
- **_store_state_payload()** (9 connections) — `src/hal0/api/routes/settings.py`
- **set_model_store()** (9 connections) — `src/hal0/api/routes/settings.py`
- **Request** (7 connections)
- **ConfigInvalidError** (6 connections) — `src/hal0/api/routes/settings.py`
- **get_settings()** (6 connections) — `src/hal0/api/routes/settings.py`
- **reload_settings()** (6 connections) — `src/hal0/api/routes/settings.py`
- **get_model_store()** (6 connections) — `src/hal0/api/routes/settings.py`
- **migrate_model_store()** (6 connections) — `src/hal0/api/routes/settings.py`
- **_validation_error_details()** (5 connections) — `src/hal0/api/routes/settings.py`
- **_deep_merge()** (4 connections) — `src/hal0/api/routes/settings.py`
- **settings_schema()** (4 connections) — `src/hal0/api/routes/settings.py`
- **get_apply_plan()** (3 connections) — `src/hal0/api/routes/settings.py`
- **ValidationError** (1 connections)
- **MigrationPlan** (1 connections)
- **Settings (config) endpoints (mounted under /api/settings).  Typed read/write of** (1 connections) — `src/hal0/api/routes/settings.py`
- **Schema validation failure — typed so the envelope carries field paths.** (1 connections) — `src/hal0/api/routes/settings.py`
- **Recursive dict merge: patch wins, but nested dicts are merged not replaced.** (1 connections) — `src/hal0/api/routes/settings.py`
- **Render a pydantic ValidationError into ``{field_path: message}``.** (1 connections) — `src/hal0/api/routes/settings.py`
- **Project a Hal0Config into a JSON-safe dict, scrubbing sensitive keys.      Every** (1 connections) — `src/hal0/api/routes/settings.py`
- **Return the current Hal0Config as JSON.      The dashboard's Settings view reads** (1 connections) — `src/hal0/api/routes/settings.py`
- *... and 9 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (12 shared connections)
- [test_model_store.py](test_model_store.py.md) (4 shared connections)
- [Hal0Error](Hal0Error.md) (3 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [test_redact.py](test_redact.py.md) (1 shared connections)
- [apply_plan](apply_plan.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/settings.py`

## Audit Trail

- EXTRACTED: 136 (89%)
- INFERRED: 16 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*