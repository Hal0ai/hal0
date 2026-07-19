# ensure_gateway_api_server_key

> 8 nodes

## Key Concepts

- **ensure_gateway_api_server_key()** (7 connections) — `src/hal0/agents/hermes_provision.py`
- **_read_secrets_env()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_is_strong_api_server_key()** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **ApiServerKeyResult** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **True iff ``value`` is a real, cryptographically-strong gateway key.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Parse ``KEY=VALUE`` lines from the secrets vault (best-effort read).** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Outcome of :func:`ensure_gateway_api_server_key`.      ``outcome`` is ``"generat** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Idempotently ensure a strong ``API_SERVER_KEY`` in the gateway vault.      Reads** (1 connections) — `src/hal0/agents/hermes_provision.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (4 shared connections)
- [Path](Path.md) (1 shared connections)
- [_StepCtx](_StepCtx.md) (1 shared connections)
- [probe.py](probe.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 20 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*