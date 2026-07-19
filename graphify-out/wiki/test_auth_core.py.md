# test_auth_core.py

> 50 nodes · cohesion 0.07

## Key Concepts

- **test_auth_core.py** (36 connections) — `tests/api/test_auth_core.py`
- **MonkeyPatch** (25 connections)
- **_scope()** (10 connections) — `tests/api/test_auth_core.py`
- **auth_client()** (5 connections) — `tests/api/test_auth_core.py`
- **test_require_auth_persisted_config_enables()** (5 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_from_scope_accepts_wrapper_object()** (5 connections) — `tests/api/test_auth_core.py`
- **isolate_secret()** (4 connections) — `tests/api/test_auth_core.py`
- **test_require_auth_env_override_beats_persisted_config()** (4 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_admin_key_wins_over_client_key()** (4 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_cookie_beats_bearer()** (4 connections) — `tests/api/test_auth_core.py`
- **test_require_auth_bind_host_no_longer_auto_enables()** (3 connections) — `tests/api/test_auth_core.py`
- **test_require_auth_key_presence_no_longer_auto_enables()** (3 connections) — `tests/api/test_auth_core.py`
- **test_require_toggle_persists_and_applies_live()** (3 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_api_key_query_param()** (3 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_bearer_admin()** (3 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_bearer_client()** (3 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_bearer_wrong_key_is_anon()** (3 connections) — `tests/api/test_auth_core.py`
- **test_resolve_principal_invalid_cookie_falls_through_to_bearer()** (3 connections) — `tests/api/test_auth_core.py`
- **Path** (2 connections)
- **test_decide_bootstrap_becomes_admin_once_keyed()** (2 connections) — `tests/api/test_auth_core.py`
- **test_decide_bootstrap_open_without_admin_key()** (2 connections) — `tests/api/test_auth_core.py`
- **test_dev_open_bypass_reaches_admin_route_with_no_creds()** (2 connections) — `tests/api/test_auth_core.py`
- **test_has_admin_key()** (2 connections) — `tests/api/test_auth_core.py`
- **test_login_rejects_wrong_key()** (2 connections) — `tests/api/test_auth_core.py`
- **test_login_success_sets_session_cookie()** (2 connections) — `tests/api/test_auth_core.py`
- *... and 25 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (4 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [test_v1_chat_slot_alias.py](test_v1_chat_slot_alias.py.md) (1 shared connections)

## Source Files

- `tests/api/test_auth_core.py`

## Audit Trail

- EXTRACTED: 166 (97%)
- INFERRED: 6 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*