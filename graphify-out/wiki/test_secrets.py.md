# test_secrets.py

> 23 nodes · cohesion 0.15

## Key Concepts

- **test_secrets.py** (15 connections) — `tests/api/test_secrets.py`
- **TestClient** (11 connections)
- **_api_env_path()** (8 connections) — `tests/api/test_secrets.py`
- **test_set_coexists_with_provider_credentials()** (4 connections) — `tests/api/test_secrets.py`
- **test_set_rejects_control_chars_in_value()** (4 connections) — `tests/api/test_secrets.py`
- **test_writer_guard_rejects_line_breaks()** (4 connections) — `tests/api/test_secrets.py`
- **test_delete_removes_secret()** (3 connections) — `tests/api/test_secrets.py`
- **test_hf_token_secret_roundtrip()** (3 connections) — `tests/api/test_secrets.py`
- **test_set_overwrites_existing_line()** (3 connections) — `tests/api/test_secrets.py`
- **test_set_secret_post_persists_and_redacts()** (3 connections) — `tests/api/test_secrets.py`
- **test_set_secret_put_also_supported()** (3 connections) — `tests/api/test_secrets.py`
- **Path** (2 connections)
- **_restore_environ()** (2 connections) — `tests/api/test_secrets.py`
- **test_delete_is_idempotent()** (2 connections) — `tests/api/test_secrets.py`
- **test_invalid_names_rejected()** (2 connections) — `tests/api/test_secrets.py`
- **test_list_empty_when_no_api_env()** (2 connections) — `tests/api/test_secrets.py`
- **test_list_returns_names_never_values()** (2 connections) — `tests/api/test_secrets.py`
- **Tests for the /api/secrets router (operator-managed secret store).  Covers GET (** (1 connections) — `tests/api/test_secrets.py`
- **Newline/CR/control chars in a value must 400 (env-var injection guard)     and l** (1 connections) — `tests/api/test_secrets.py`
- **Defense-in-depth: the shared writer refuses the full str.splitlines()     set —** (1 connections) — `tests/api/test_secrets.py`
- **Secrets share api.env with provider creds — both lines survive.** (1 connections) — `tests/api/test_secrets.py`
- **P4: HF_TOKEN behaves like any secret — set → listed + live in os.environ;     de** (1 connections) — `tests/api/test_secrets.py`
- **Snapshot + restore os.environ — the router mutates it on set/delete.** (1 connections) — `tests/api/test_secrets.py`

## Relationships

- [upsert_env_value](upsert_env_value.md) (1 shared connections)

## Source Files

- `tests/api/test_secrets.py`

## Audit Trail

- EXTRACTED: 78 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*