# _Recorder

> 31 nodes

## Key Concepts

- **_Recorder** (21 connections) — `tests/board/test_board_client.py`
- **test_board_client.py** (19 connections) — `tests/board/test_board_client.py`
- **_make_client()** (17 connections) — `tests/board/test_board_client.py`
- **MonkeyPatch** (7 connections)
- **test_upstream_4xx_passes_status_through()** (5 connections) — `tests/board/test_board_client.py`
- **test_upstream_5xx_maps_to_502()** (5 connections) — `tests/board/test_board_client.py`
- **test_harvests_token_from_dashboard_html()** (5 connections) — `tests/board/test_board_client.py`
- **test_harvested_token_is_cached()** (5 connections) — `tests/board/test_board_client.py`
- **test_env_pin_skips_html_harvest()** (5 connections) — `tests/board/test_board_client.py`
- **.from_env()** (4 connections) — `src/hal0/board/__init__.py`
- **.respond()** (4 connections) — `tests/board/test_board_client.py`
- **.respond_text()** (4 connections) — `tests/board/test_board_client.py`
- **.handler()** (4 connections) — `tests/board/test_board_client.py`
- **test_empty_200_body_returns_empty_dict()** (4 connections) — `tests/board/test_board_client.py`
- **test_default_agent_id_from_env()** (4 connections) — `tests/board/test_board_client.py`
- **test_transport_failure_maps_to_503()** (4 connections) — `tests/board/test_board_client.py`
- **test_401_reharvests_token_and_retries()** (4 connections) — `tests/board/test_board_client.py`
- **test_from_env_default_base_url()** (3 connections) — `tests/board/test_board_client.py`
- **test_from_env_reads_base_url()** (3 connections) — `tests/board/test_board_client.py`
- **test_request_json_prepends_kanban_prefix()** (3 connections) — `tests/board/test_board_client.py`
- **test_request_json_forwards_params_and_body()** (3 connections) — `tests/board/test_board_client.py`
- **test_headers_inject_token_both_forms()** (3 connections) — `tests/board/test_board_client.py`
- **test_headers_no_token_omits_auth()** (3 connections) — `tests/board/test_board_client.py`
- **test_agent_id_override_outbound()** (3 connections) — `tests/board/test_board_client.py`
- **Any** (2 connections)
- *... and 6 more nodes in this community*

## Relationships

- [HermesKanbanClient](HermesKanbanClient.md) (6 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/board/__init__.py`
- `tests/board/test_board_client.py`

## Audit Trail

- EXTRACTED: 146 (97%)
- INFERRED: 5 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*