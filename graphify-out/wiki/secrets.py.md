# secrets.py

> 32 nodes

## Key Concepts

- **secrets.py** (25 connections) — `src/hal0/api/routes/secrets.py`
- **_set_secret()** (13 connections) — `src/hal0/api/routes/secrets.py`
- **delete_secret()** (9 connections) — `src/hal0/api/routes/secrets.py`
- **SecretBody** (6 connections) — `src/hal0/api/routes/secrets.py`
- **_api_env()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **list_secrets()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **set_secret_post()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **set_secret_put()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **_validate_name()** (5 connections) — `src/hal0/api/routes/secrets.py`
- **_emit()** (5 connections) — `src/hal0/api/routes/secrets.py`
- **Request** (5 connections)
- **SecretWriteFailed** (4 connections) — `src/hal0/api/routes/secrets.py`
- **_updated_at()** (4 connections) — `src/hal0/api/routes/secrets.py`
- **_validate_value()** (4 connections) — `src/hal0/api/routes/secrets.py`
- **Response** (4 connections)
- **SecretNameInvalid** (3 connections) — `src/hal0/api/routes/secrets.py`
- **SecretValueInvalid** (3 connections) — `src/hal0/api/routes/secrets.py`
- **SecretNotFound** (2 connections) — `src/hal0/api/routes/secrets.py`
- **Path** (2 connections)
- **Any** (1 connections)
- **Operator-managed secrets store (mounted under ``/api/secrets``).  Backs the dash** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Set-secret request body — a single opaque value.** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Resolve the api.env path (HAL0_HOME-relative under tests).** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Return the validated secret name or raise :class:`SecretNameInvalid`.** (1 connections) — `src/hal0/api/routes/secrets.py`
- **File-mtime (ISO 8601 UTC) of api.env, or None if it doesn't exist.      An ``Env** (1 connections) — `src/hal0/api/routes/secrets.py`
- *... and 7 more nodes in this community*

## Relationships

- [Hal0Error](Hal0Error.md) (4 shared connections)
- [upsert_env_value](upsert_env_value.md) (3 shared connections)
- [_auth.py](_auth.py.md) (1 shared connections)
- [benchmarks.py](benchmarks.py.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [runner.py](runner.py.md) (1 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [build_workflow](build_workflow.md) (1 shared connections)
- [pull.py](pull.py.md) (1 shared connections)
- [service_identity.py](service_identity.py.md) (1 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/secrets.py`

## Audit Trail

- EXTRACTED: 128 (98%)
- INFERRED: 3 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*