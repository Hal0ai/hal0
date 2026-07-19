# test_chat_proxy_auth.py

> 39 nodes · cohesion 0.07

## Key Concepts

- **test_chat_proxy_auth.py** (18 connections) — `tests/api/test_chat_proxy_auth.py`
- **TestClient** (7 connections)
- **client()** (6 connections) — `tests/api/test_chat_proxy_auth.py`
- **_get_cookie_value()** (4 connections) — `tests/api/test_chat_proxy_auth.py`
- **isolate_secret()** (4 connections) — `tests/api/test_chat_proxy_auth.py`
- **MonkeyPatch** (4 connections)
- **Path** (4 connections)
- **test_ws_upgrade_with_disallowed_origin_rejected()** (4 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_allowed_origins_empty_env_falls_back()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_allowed_origins_env_override()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_handshake_sets_session_cookie()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_rest_session_create_requires_cookie()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_secret_file_chmod_0600()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_secret_reused_across_mints()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_ws_upgrade_with_bad_cookie_rejected()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_ws_upgrade_with_missing_cookie_rejected()** (3 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_mint_then_verify_roundtrip()** (2 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_verify_rejects_expired_cookie()** (2 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_verify_rejects_garbage()** (2 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_verify_rejects_tampered_payload()** (2 connections) — `tests/api/test_chat_proxy_auth.py`
- **test_verify_rejects_tampered_signature()** (2 connections) — `tests/api/test_chat_proxy_auth.py`
- **Auth tests for the chat-proxy WS surface.  DA-sec-ops MUST-FIX #2: the WS routes** (1 connections) — `tests/api/test_chat_proxy_auth.py`
- **An empty override falls back to the default allowlist (dev convenience).** (1 connections) — `tests/api/test_chat_proxy_auth.py`
- **Spin up the app with isolated secret + HAL0_HOME.      We intentionally don't re** (1 connections) — `tests/api/test_chat_proxy_auth.py`
- **Drive the handshake endpoint + read the cookie out of the response.** (1 connections) — `tests/api/test_chat_proxy_auth.py`
- *... and 14 more nodes in this community*

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_proxy_auth.py`

## Audit Trail

- EXTRACTED: 102 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*