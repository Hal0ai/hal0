# _auth.py

> 23 nodes · cohesion 0.12

## Key Concepts

- **_auth.py** (12 connections) — `src/hal0/api/agents/_auth.py`
- **check_ws_origin_and_cookie()** (7 connections) — `src/hal0/api/agents/_auth.py`
- **verify_session_cookie()** (7 connections) — `src/hal0/api/agents/_auth.py`
- **set_session_cookie()** (6 connections) — `src/hal0/api/agents/_auth.py`
- **_load_or_create_secret()** (5 connections) — `src/hal0/api/agents/_auth.py`
- **mint_session_cookie()** (5 connections) — `src/hal0/api/agents/_auth.py`
- **allowed_origins()** (4 connections) — `src/hal0/api/agents/_auth.py`
- **_secret_path()** (4 connections) — `src/hal0/api/agents/_auth.py`
- **_b64url_decode()** (3 connections) — `src/hal0/api/agents/_auth.py`
- **_b64url_encode()** (3 connections) — `src/hal0/api/agents/_auth.py`
- **Path** (1 connections)
- **Response** (1 connections)
- **WebSocket** (1 connections)
- **Origin allowlist + HMAC session-cookie helpers for the agent chat proxy.  Bearer** (1 connections) — `src/hal0/api/agents/_auth.py`
- **URL-safe base64 without padding (matches JWT conventions).** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Reverse of :func:`_b64url_encode`. Restores padding before decode.** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Effective allowlist, including the env override when set.      A misconfigured e** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Generate a fresh signed session cookie value.      The payload is JSON-serialise** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Return ``True`` iff the cookie's signature is valid AND unexpired.      Constant** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Mint + attach a session cookie to ``response``. Returns its value.      ``secure** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Return ``True`` iff Origin is allowlisted AND cookie is valid.      Used as the** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Resolve the on-disk path for the HMAC secret.      Honours ``HAL0_AGENT_SECRET_P** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Return the HMAC secret, generating it on first call.      The file is created wi** (1 connections) — `src/hal0/api/agents/_auth.py`

## Relationships

- [chat_proxy.py](chat_proxy.py.md) (3 shared connections)
- [auth.py](auth.py.md) (3 shared connections)
- [_proxy_ws](_proxy_ws.md) (2 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/agents/_auth.py`

## Audit Trail

- EXTRACTED: 63 (91%)
- INFERRED: 6 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*