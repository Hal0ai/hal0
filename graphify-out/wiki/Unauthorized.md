# Unauthorized

> 23 nodes

## Key Concepts

- **Unauthorized** (12 connections) — `src/hal0/agents/hermes/core/errors.py`
- **auth.py** (12 connections) — `src/hal0/api/routes/auth.py`
- **login()** (9 connections) — `src/hal0/api/routes/auth.py`
- **set_require_auth()** (8 connections) — `src/hal0/api/routes/auth.py`
- **rotate_key()** (7 connections) — `src/hal0/api/routes/auth.py`
- **_client_ip()** (5 connections) — `src/hal0/api/routes/auth.py`
- **Request** (5 connections)
- **TooManyRequests** (5 connections) — `src/hal0/errors.py`
- **LoginRequest** (4 connections) — `src/hal0/api/routes/auth.py`
- **RequireAuthRequest** (4 connections) — `src/hal0/api/routes/auth.py`
- **RotateKeyRequest** (4 connections) — `src/hal0/api/routes/auth.py`
- **logout()** (3 connections) — `src/hal0/api/routes/auth.py`
- **_Color** (3 connections) — `tests/api/test_typed_errors.py`
- **_Body** (3 connections) — `tests/api/test_typed_errors.py`
- **Response** (2 connections)
- **The supplied credential was rejected.** (1 connections) — `src/hal0/agents/hermes/core/errors.py`
- **KB-1 / §1 auth surface: ``POST /api/auth/login`` + ``GET /api/auth/status``.  Bo** (1 connections) — `src/hal0/api/routes/auth.py`
- **Best-effort caller IP for the login rate-limit key.      Falls back to a constan** (1 connections) — `src/hal0/api/routes/auth.py`
- **Validate ``key`` against ``HAL0_ADMIN_KEY`` and mint the session cookie.      Re** (1 connections) — `src/hal0/api/routes/auth.py`
- **Clear the browser session cookie so the operator returns to anonymous.      OPEN** (1 connections) — `src/hal0/api/routes/auth.py`
- **Persist the ``[security].require_auth`` enforcement toggle. ADMIN-gated.      Wr** (1 connections) — `src/hal0/api/routes/auth.py`
- **Rotate the ``admin`` or ``client`` box key. ADMIN-gated, status-only.      Mints** (1 connections) — `src/hal0/api/routes/auth.py`
- **429 — the caller has exceeded a rate budget and should back off.      Use for br** (1 connections) — `src/hal0/errors.py`

## Relationships

- [_client](_client.md) (5 shared connections)
- [auth.py](auth.py.md) (4 shared connections)
- [BaseModel](BaseModel.md) (4 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [service_identity.py](service_identity.py.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [test_typed_errors.py](test_typed_errors.py.md) (2 shared connections)
- [._request](_request.md) (1 shared connections)
- [_auth.py](_auth.py.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [die](die.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes/core/errors.py`
- `src/hal0/api/routes/auth.py`
- `src/hal0/errors.py`
- `tests/api/test_typed_errors.py`

## Audit Trail

- EXTRACTED: 72 (77%)
- INFERRED: 22 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*