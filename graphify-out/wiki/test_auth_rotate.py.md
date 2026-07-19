# test_auth_rotate.py

> 45 nodes · cohesion 0.10

## Key Concepts

- **test_auth_rotate.py** (19 connections) — `tests/api/test_auth_rotate.py`
- **MonkeyPatch** (14 connections)
- **TestClient** (14 connections)
- **service_identity.py** (12 connections) — `src/hal0/service_identity.py`
- **Path** (10 connections)
- **keys_from_api_env()** (8 connections) — `src/hal0/service_identity.py`
- **_make_client()** (7 connections) — `tests/api/test_auth_rotate.py`
- **rotate_api_env_key()** (6 connections) — `src/hal0/service_identity.py`
- **service_key()** (6 connections) — `src/hal0/service_identity.py`
- **_arm_auth()** (6 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_applies_live_new_key_works_old_fails()** (6 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_preserves_other_env_lines()** (6 connections) — `tests/api/test_auth_rotate.py`
- **service_auth_headers()** (5 connections) — `src/hal0/service_identity.py`
- **rotate_client()** (5 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_admin_never_leaks_the_key()** (5 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_admin_writes_api_env_status_only()** (5 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_client_tier()** (5 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_preserves_non_world_readable_mode()** (5 connections) — `tests/api/test_auth_rotate.py`
- **_api_env_path()** (4 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_allowed_admin_bearer_when_armed()** (4 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_denied_anon_when_armed()** (4 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_denied_client_bearer_when_armed()** (4 connections) — `tests/api/test_auth_rotate.py`
- **test_rotate_is_rate_limited()** (4 connections) — `tests/api/test_auth_rotate.py`
- **_key_from_api_env()** (3 connections) — `src/hal0/cli/_shared.py`
- **generate_service_key()** (3 connections) — `src/hal0/service_identity.py`
- *... and 20 more nodes in this community*

## Relationships

- [_shared.py](_shared.py.md) (2 shared connections)
- [auth.py](auth.py.md) (2 shared connections)
- [chat.py](chat.py.md) (2 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/cli/_shared.py`
- `src/hal0/service_identity.py`
- `tests/api/test_auth_rotate.py`

## Audit Trail

- EXTRACTED: 185 (92%)
- INFERRED: 15 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*