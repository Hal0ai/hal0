# auth.py

> 20 nodes · cohesion 0.16

## Key Concepts

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
- **BaseModel** (3 connections)
- **Response** (2 connections)
- **KB-1 / §1 auth surface: ``POST /api/auth/login`` + ``GET /api/auth/status``.  Bo** (1 connections) — `src/hal0/api/routes/auth.py`
- **Persist the ``[security].require_auth`` enforcement toggle. ADMIN-gated.      Wr** (1 connections) — `src/hal0/api/routes/auth.py`
- **Rotate the ``admin`` or ``client`` box key. ADMIN-gated, status-only.      Mints** (1 connections) — `src/hal0/api/routes/auth.py`
- **Best-effort caller IP for the login rate-limit key.      Falls back to a constan** (1 connections) — `src/hal0/api/routes/auth.py`
- **Validate ``key`` against ``HAL0_ADMIN_KEY`` and mint the session cookie.      Re** (1 connections) — `src/hal0/api/routes/auth.py`
- **Clear the browser session cookie so the operator returns to anonymous.      OPEN** (1 connections) — `src/hal0/api/routes/auth.py`
- **429 — the caller has exceeded a rate budget and should back off.      Use for br** (1 connections) — `src/hal0/errors.py`

## Relationships

- [auth.py](auth.py.md) (4 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [client.py](client.py.md) (3 shared connections)
- [test_auth_rotate.py](test_auth_rotate.py.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [_auth.py](_auth.py.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/auth.py`
- `src/hal0/errors.py`

## Audit Trail

- EXTRACTED: 64 (82%)
- INFERRED: 14 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*