# test_settings_routes.py

> 19 nodes · cohesion 0.15

## Key Concepts

- **test_settings_routes.py** (9 connections) — `tests/api/test_settings_routes.py`
- **TestClient** (8 connections)
- **isolated_client()** (4 connections) — `tests/api/test_settings_routes.py`
- **test_get_settings_returns_default_config_when_no_toml()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_put_settings_non_json_body_returns_envelope()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_put_settings_partial_update_persists_to_disk()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_put_settings_validation_error_envelope()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_reload_after_bad_toml_returns_parse_error_envelope()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_reload_settings_re_reads_disk()** (3 connections) — `tests/api/test_settings_routes.py`
- **test_settings_schema_returns_json_schema()** (3 connections) — `tests/api/test_settings_routes.py`
- **Tests for /api/settings — typed read/write of hal0.toml.  Uses ``tmp_hal0_home``** (1 connections) — `tests/api/test_settings_routes.py`
- **GET /api/settings/schema returns the pydantic JSON schema.** (1 connections) — `tests/api/test_settings_routes.py`
- **Malformed TOML on disk surfaces as the typed config.parse_error envelope.** (1 connections) — `tests/api/test_settings_routes.py`
- **A TestClient whose lifespan resolves paths under tmp_hal0_home.      The shared** (1 connections) — `tests/api/test_settings_routes.py`
- **GET /api/settings on a fresh install returns the all-defaults shape.** (1 connections) — `tests/api/test_settings_routes.py`
- **PUT /api/settings deep-merges and writes hal0.toml atomically.** (1 connections) — `tests/api/test_settings_routes.py`
- **Schema-failing payload returns config.invalid with per-field details.** (1 connections) — `tests/api/test_settings_routes.py`
- **Non-JSON body fails with the typed envelope, not a stack trace.** (1 connections) — `tests/api/test_settings_routes.py`
- **POST /api/settings/reload re-reads hal0.toml from disk.** (1 connections) — `tests/api/test_settings_routes.py`

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_settings_routes.py`

## Audit Trail

- EXTRACTED: 50 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*