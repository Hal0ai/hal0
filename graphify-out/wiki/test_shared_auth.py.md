# test_shared_auth.py

> 10 nodes

## Key Concepts

- **test_shared_auth.py** (8 connections) — `tests/cli/test_shared_auth.py`
- **MonkeyPatch** (7 connections)
- **_clean_env()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_auth_headers_prefers_admin_env()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_auth_headers_falls_back_to_client_env()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_auth_headers_reads_api_env_file()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_auth_headers_empty_when_nothing_discoverable()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_api_request_attaches_bearer()** (2 connections) — `tests/cli/test_shared_auth.py`
- **test_api_request_respects_caller_authorization()** (2 connections) — `tests/cli/test_shared_auth.py`
- **CLI→API auth attachment (halo150 O2): _api_request sends a bearer token discover** (1 connections) — `tests/cli/test_shared_auth.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/cli/test_shared_auth.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*