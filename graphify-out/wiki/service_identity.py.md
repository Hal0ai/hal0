# service_identity.py

> 16 nodes

## Key Concepts

- **service_identity.py** (12 connections) — `src/hal0/service_identity.py`
- **service_key()** (6 connections) — `src/hal0/service_identity.py`
- **rotate_api_env_key()** (6 connections) — `src/hal0/service_identity.py`
- **service_auth_headers()** (5 connections) — `src/hal0/service_identity.py`
- **_tier_order()** (3 connections) — `src/hal0/service_identity.py`
- **generate_service_key()** (3 connections) — `src/hal0/service_identity.py`
- **key_fingerprint()** (3 connections) — `src/hal0/service_identity.py`
- **_upsert_env_line()** (3 connections) — `src/hal0/service_identity.py`
- **Box service identity — the API keys hal0 processes present on internal calls.  W** (1 connections) — `src/hal0/service_identity.py`
- **The (first, fallback) tier order for a ``prefer`` selector.** (1 connections) — `src/hal0/service_identity.py`
- **Resolve the box service key, preferring the ``prefer`` tier.      Order: env[pre** (1 connections) — `src/hal0/service_identity.py`
- **``{"Authorization": "Bearer <key>"}`` for the box identity, or ``{}``.** (1 connections) — `src/hal0/service_identity.py`
- **Mint a fresh, strong box service key.      Mirrors the gateway's ``_generate_api** (1 connections) — `src/hal0/service_identity.py`
- **Short, non-reversible fingerprint of ``key`` (sha256 hex, first 8 chars).      L** (1 connections) — `src/hal0/service_identity.py`
- **Return ``text`` with ``name=value`` set.      Replaces the FIRST existing ``name** (1 connections) — `src/hal0/service_identity.py`
- **Rotate the ``tier`` (``admin``|``client``) box key in ``/etc/hal0/api.env``.** (1 connections) — `src/hal0/service_identity.py`

## Relationships

- [test_auth_rotate.py](test_auth_rotate.py.md) (3 shared connections)
- [Unauthorized](Unauthorized.md) (2 shared connections)
- [chat.py](chat.py.md) (2 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)

## Source Files

- `src/hal0/service_identity.py`

## Audit Trail

- EXTRACTED: 45 (92%)
- INFERRED: 4 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*