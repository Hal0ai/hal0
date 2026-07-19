# test_providers.py

> 22 nodes · cohesion 0.14

## Key Concepts

- **test_providers.py** (11 connections) — `tests/api/test_providers.py`
- **TestClient** (8 connections)
- **_api_env_path()** (6 connections) — `tests/api/test_providers.py`
- **client()** (5 connections) — `tests/api/test_providers.py`
- **test_credential_write_rejects_newline_value()** (4 connections) — `tests/api/test_providers.py`
- **test_credential_write_rewrites_existing_line()** (4 connections) — `tests/api/test_providers.py`
- **test_credential_write_sets_in_process_env()** (4 connections) — `tests/api/test_providers.py`
- **test_credential_write_persists_key_and_redacts_value()** (3 connections) — `tests/api/test_providers.py`
- **test_credential_write_rejects_malformed_keys()** (3 connections) — `tests/api/test_providers.py`
- **test_credential_write_rejects_mismatched_key()** (3 connections) — `tests/api/test_providers.py`
- **FastAPI** (2 connections)
- **test_credential_write_unknown_upstream_404()** (2 connections) — `tests/api/test_providers.py`
- **MonkeyPatch** (1 connections)
- **Path** (1 connections)
- **Tests for the /api/providers credential write route (Phase 8 closeout).  Covers** (1 connections) — `tests/api/test_providers.py`
- **Upstream declares auth_value_env=OPENROUTER_API_KEY; the writer     refuses to l** (1 connections) — `tests/api/test_providers.py`
- **Body validation must reject keys that wouldn't survive POSIX env-var     grammar** (1 connections) — `tests/api/test_providers.py`
- **A newline/CR in the VALUE must 400 (same env-var injection guard as     the secr** (1 connections) — `tests/api/test_providers.py`
- **The route updates os.environ[key] so the running registry can pick     up the ne** (1 connections) — `tests/api/test_providers.py`
- **TestClient with the openrouter upstream registered.      The upstream registry i** (1 connections) — `tests/api/test_providers.py`
- **Resolve the api.env file inside the tmp_hal0_home sandbox.** (1 connections) — `tests/api/test_providers.py`
- **Second write of the same key replaces the line in place — no     duplicate ``OPE** (1 connections) — `tests/api/test_providers.py`

## Relationships

- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `tests/api/test_providers.py`

## Audit Trail

- EXTRACTED: 64 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*