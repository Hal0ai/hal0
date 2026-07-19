# test_env_writer.py

> 26 nodes · cohesion 0.13

## Key Concepts

- **test_env_writer.py** (13 connections) — `tests/openwebui/test_env_writer.py`
- **Path** (10 connections)
- **_parse_env()** (9 connections) — `tests/openwebui/test_env_writer.py`
- **test_trusted_email_header_via_explicit_override()** (6 connections) — `tests/openwebui/test_env_writer.py`
- **test_webui_auth_is_always_false_by_default()** (6 connections) — `tests/openwebui/test_env_writer.py`
- **test_enable_persistent_config_is_false()** (5 connections) — `tests/openwebui/test_env_writer.py`
- **test_override_none_deletes_default()** (5 connections) — `tests/openwebui/test_env_writer.py`
- **test_overrides_replace_defaults()** (5 connections) — `tests/openwebui/test_env_writer.py`
- **test_write_openwebui_env_includes_voice_callmode_keys()** (5 connections) — `tests/openwebui/test_env_writer.py`
- **test_write_openwebui_env_writes_all_prewired_keys()** (5 connections) — `tests/openwebui/test_env_writer.py`
- **test_atomic_write_no_orphan_tmp()** (4 connections) — `tests/openwebui/test_env_writer.py`
- **test_override_non_string_raises()** (4 connections) — `tests/openwebui/test_env_writer.py`
- **test_write_openwebui_env_defaults_to_paths_resolver()** (4 connections) — `tests/openwebui/test_env_writer.py`
- **MonkeyPatch** (2 connections)
- **Unit tests for hal0.openwebui.env_writer.  Verifies the prewired environment fil** (1 connections) — `tests/openwebui/test_env_writer.py`
- **After a successful write, no .hal0-env-*.tmp files remain.** (1 connections) — `tests/openwebui/test_env_writer.py`
- **OpenWebUI prewires with WEBUI_AUTH=False — no login screen, no     trusted-email** (1 connections) — `tests/openwebui/test_env_writer.py`
- **Open WebUI Call mode is prewired to hal0's /v1 audio endpoints.** (1 connections) — `tests/openwebui/test_env_writer.py`
- **Operators fronting hal0 with a reverse proxy that injects a     trusted email he** (1 connections) — `tests/openwebui/test_env_writer.py`
- **ENABLE_PERSISTENT_CONFIG=False must be prewired so env vars always     win over** (1 connections) — `tests/openwebui/test_env_writer.py`
- **Parse a hal0 env file into a dict (ignoring comments / blanks).** (1 connections) — `tests/openwebui/test_env_writer.py`
- **All seven prewired vars from PLAN.md §8 are present in the file.** (1 connections) — `tests/openwebui/test_env_writer.py`
- **With no explicit path, write_openwebui_env honours HAL0_HOME.** (1 connections) — `tests/openwebui/test_env_writer.py`
- **An override key replaces the default value for that key only.** (1 connections) — `tests/openwebui/test_env_writer.py`
- **Setting an override to None removes the key from the output.** (1 connections) — `tests/openwebui/test_env_writer.py`
- *... and 1 more nodes in this community*

## Relationships

- [write_openwebui_env](write_openwebui_env.md) (11 shared connections)

## Source Files

- `tests/openwebui/test_env_writer.py`

## Audit Trail

- EXTRACTED: 85 (89%)
- INFERRED: 10 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*