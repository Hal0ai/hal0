# _FakeHttpClient

> 25 nodes · cohesion 0.12

## Key Concepts

- **_FakeHttpClient** (20 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **_FakeResponse** (16 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **.close()** (4 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **.__init__()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_add_posts_expected_payload_and_headers()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_close_does_not_close_injected_client()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_defaults_base_url_and_agent_id()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_delete_posts_ids_list()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_list_items_is_a_get_with_limit_param()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_recall_includes_types_when_given()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_recall_omits_types_by_default()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_request_falls_back_to_raw_text_on_non_json_response()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_request_raises_on_4xx_with_status_code()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_request_wraps_non_dict_json_body()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_search_posts_query_and_limit()** (3 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **.__init__()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **.json()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **Any** (2 connections)
- **test_client_add_omits_tags_and_metadata_when_none()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_add_shared_sets_private_header_zero()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_close_closes_owned_client()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **test_client_request_wraps_transport_error()** (2 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **Exception** (1 connections)
- **Duck-typed stand-in for ``httpx.Response`` — no network involved.** (1 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- **Duck-typed stand-in for ``httpx.Client`` — records calls, no sockets.** (1 connections) — `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Relationships

- [test_memory_hindsight_plugin.py](test_memory_hindsight_plugin.py.md) (18 shared connections)
- [MemoryProvider](MemoryProvider.md) (2 shared connections)
- [Hal0MemoryClient](Hal0MemoryClient.md) (2 shared connections)
- [_client](_client.md) (1 shared connections)

## Source Files

- `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`

## Audit Trail

- EXTRACTED: 89 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*