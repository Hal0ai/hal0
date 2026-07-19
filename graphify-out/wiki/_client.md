# _client

> 26 nodes · cohesion 0.21

## Key Concepts

- **_client()** (20 connections) — `tests/agents/hermes/core/test_client.py`
- **test_client.py** (18 connections) — `tests/agents/hermes/core/test_client.py`
- **MonkeyPatch** (17 connections)
- **Unavailable** (15 connections) — `src/hal0/agents/hermes/core/errors.py`
- **.request()** (8 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_does_not_retry_other_statuses()** (6 connections) — `tests/agents/hermes/core/test_client.py`
- **test_retries_connect_and_timeout_three_times()** (5 connections) — `tests/agents/hermes/core/test_client.py`
- **test_status_and_error_codes_decode_to_typed_errors()** (5 connections) — `tests/agents/hermes/core/test_client.py`
- **test_diagnostics_redact_keys()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_health_sends_no_authorization_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_keyed_mutation_retries_transient_failure_three_times()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_mutations_send_only_admin_key_and_idempotency_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_reads_send_only_client_key()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_rejects_caller_auth_override()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_rejects_non_relative_targets_before_sending_credentials()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_rejects_transport_owned_header_overrides()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_relative_target_preserves_configured_base_path()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_retries_transient_statuses_three_times()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_unauthorized_differs_from_connection_failure()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_unkeyed_mutation_does_not_retry_transient_failure()** (4 connections) — `tests/agents/hermes/core/test_client.py`
- **test_each_request_has_generated_correlation_id()** (3 connections) — `tests/agents/hermes/core/test_client.py`
- **RequestError** (1 connections)
- **hal0 could not complete the request.** (1 connections) — `src/hal0/agents/hermes/core/errors.py`
- **Request** (1 connections)
- **Response** (1 connections)
- *... and 1 more nodes in this community*

## Relationships

- [client.py](client.py.md) (8 shared connections)
- [Hal0HermesClient](Hal0HermesClient.md) (5 shared connections)
- [_FakeHttpClient](_FakeHttpClient.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes/core/errors.py`
- `tests/agents/hermes/core/test_client.py`
- `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Audit Trail

- EXTRACTED: 117 (78%)
- INFERRED: 33 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*