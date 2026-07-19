# secrets.py

> 28 nodes · cohesion 0.14

## Key Concepts

- **secrets.py** (25 connections) — `src/hal0/api/routes/secrets.py`
- **_set_secret()** (13 connections) — `src/hal0/api/routes/secrets.py`
- **delete_secret()** (9 connections) — `src/hal0/api/routes/secrets.py`
- **_api_env()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **SecretBody** (6 connections) — `src/hal0/api/routes/secrets.py`
- **set_secret_post()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **set_secret_put()** (6 connections) — `src/hal0/api/routes/secrets.py`
- **_emit()** (5 connections) — `src/hal0/api/routes/secrets.py`
- **Request** (5 connections)
- **_validate_name()** (5 connections) — `src/hal0/api/routes/secrets.py`
- **Hal0Error** (4 connections)
- **Response** (4 connections)
- **SecretWriteFailed** (4 connections) — `src/hal0/api/routes/secrets.py`
- **_validate_value()** (4 connections) — `src/hal0/api/routes/secrets.py`
- **SecretNameInvalid** (3 connections) — `src/hal0/api/routes/secrets.py`
- **SecretValueInvalid** (3 connections) — `src/hal0/api/routes/secrets.py`
- **SecretNotFound** (2 connections) — `src/hal0/api/routes/secrets.py`
- **BaseModel** (1 connections)
- **Operator-managed secrets store (mounted under ``/api/secrets``).  Backs the dash** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Return the validated secret name or raise :class:`SecretNameInvalid`.** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Best-effort footer journal event — name only, never the value.** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Reject empty / non-printable values.      A control character — most dangerously** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Shared set/overwrite implementation for POST + PUT.** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Set/overwrite a secret (POST form — matches the v3 ``useSecretSet`` hook).** (1 connections) — `src/hal0/api/routes/secrets.py`
- **Set/overwrite a secret (PUT form — documented contract).** (1 connections) — `src/hal0/api/routes/secrets.py`
- *... and 3 more nodes in this community*

## Relationships

- [list_secrets](list_secrets.md) (4 shared connections)
- [upsert_env_value](upsert_env_value.md) (2 shared connections)
- [_auth.py](_auth.py.md) (1 shared connections)
- [benchmarks.py](benchmarks.py.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [runner.py](runner.py.md) (1 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [comfyui_workflows.py](comfyui_workflows.py.md) (1 shared connections)
- [Model](Model.md) (1 shared connections)
- [test_auth_rotate.py](test_auth_rotate.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/secrets.py`

## Audit Trail

- EXTRACTED: 119 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*