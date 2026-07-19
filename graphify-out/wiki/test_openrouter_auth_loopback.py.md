# test_openrouter_auth_loopback.py

> 37 nodes

## Key Concepts

- **test_openrouter_auth_loopback.py** (16 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **require_loopback()** (11 connections) — `src/hal0/api/openrouter/_loopback.py`
- **TestClient** (7 connections)
- **FastAPI** (7 connections)
- **is_loopback_host()** (6 connections) — `src/hal0/api/openrouter/_loopback.py`
- **test_callback_mounted_when_env_is_one()** (6 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_callback_mounted_when_env_is_true()** (6 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **callback_client()** (4 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_callback_from_loopback_returns_501_with_adr_pointer()** (4 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_callback_from_non_loopback_returns_403()** (4 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_callback_not_mounted_when_env_unset()** (4 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_callback_not_mounted_for_unknown_env_value()** (4 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **_loopback.py** (3 connections) — `src/hal0/api/openrouter/_loopback.py`
- **test_require_loopback_helper_raises_for_lan_request()** (3 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_require_loopback_helper_passes_for_loopback_request()** (3 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_require_loopback_helper_handles_missing_client()** (3 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **gated_app()** (3 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_is_loopback_host_accepts_loopback_literals()** (2 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_is_loopback_host_rejects_lan_and_public()** (2 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **test_is_loopback_host_rejects_none()** (2 connections) — `tests/api/test_openrouter_auth_loopback.py`
- **MonkeyPatch** (2 connections)
- **Request** (1 connections)
- **Loopback-only guard helpers for the OpenRouter OAuth callback.  The callback is** (1 connections) — `src/hal0/api/openrouter/_loopback.py`
- **Return ``True`` only for loopback client hosts.      Accepts the IPv4 loopback (** (1 connections) — `src/hal0/api/openrouter/_loopback.py`
- **Raise ``HTTPException(403)`` for non-loopback callers.      Designed to be calle** (1 connections) — `src/hal0/api/openrouter/_loopback.py`
- *... and 12 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/hal0/api/openrouter/_loopback.py`
- `tests/api/test_openrouter_auth_loopback.py`

## Audit Trail

- EXTRACTED: 98 (83%)
- INFERRED: 20 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*