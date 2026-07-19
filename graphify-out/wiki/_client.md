# _client

> 46 nodes

## Key Concepts

- **_client()** (20 connections) — `tests/agents/hermes/core/test_client.py`
- **test_client.py** (18 connections) — `tests/agents/hermes/core/test_client.py`
- **Hal0HermesClient** (17 connections) — `src/hal0/agents/hermes/core/client.py`
- **MonkeyPatch** (17 connections)
- **Unavailable** (15 connections) — `src/hal0/agents/hermes/core/errors.py`
- **client.py** (12 connections) — `src/hal0/agents/hermes/core/client.py`
- **IncompatibleSchema** (9 connections) — `src/hal0/agents/hermes/core/errors.py`
- **MissingResource** (9 connections) — `src/hal0/agents/hermes/core/errors.py`
- **__init__.py** (8 connections) — `src/hal0/agents/hermes/core/__init__.py`
- **errors.py** (8 connections) — `src/hal0/agents/hermes/core/errors.py`
- **HermesTransportError** (8 connections) — `src/hal0/agents/hermes/core/errors.py`
- **.request()** (8 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_does_not_retry_other_statuses()** (6 connections) — `tests/agents/hermes/core/test_client.py`
- **test_retries_connect_and_timeout_three_times()** (5 connections) — `tests/agents/hermes/core/test_client.py`
- **test_status_and_error_codes_decode_to_typed_errors()** (5 connections) — `tests/agents/hermes/core/test_client.py`
- **test_health_sends_no_authorization_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_reads_send_only_client_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_mutations_send_only_admin_key_and_idempotency_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_unauthorized_differs_from_connection_failure()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_diagnostics_redact_keys()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_retries_transient_statuses_three_times()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_rejects_non_relative_targets_before_sending_credentials()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_relative_target_preserves_configured_base_path()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_unkeyed_mutation_does_not_retry_transient_failure()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_keyed_mutation_retries_transient_failure_three_times()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- *... and 21 more nodes in this community*

## Relationships

- [._request](_request.md) (12 shared connections)
- [Unauthorized](Unauthorized.md) (5 shared connections)
- [RuntimeError](RuntimeError.md) (1 shared connections)
- [test_memory_hindsight_plugin.py](test_memory_hindsight_plugin.py.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes/core/__init__.py`
- `src/hal0/agents/hermes/core/client.py`
- `src/hal0/agents/hermes/core/errors.py`
- `tests/agents/hermes/core/test_client.py`
- `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Audit Trail

- EXTRACTED: 194 (82%)
- INFERRED: 43 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*