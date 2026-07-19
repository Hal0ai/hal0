# _Recorder

> 56 nodes · cohesion 0.07

## Key Concepts

- **_Recorder** (21 connections) — `tests/board/test_board_client.py`
- **test_board_client.py** (19 connections) — `tests/board/test_board_client.py`
- **_make_client()** (17 connections) — `tests/board/test_board_client.py`
- **HermesKanbanClient** (13 connections) — `src/hal0/board/__init__.py`
- **__init__.py** (7 connections) — `src/hal0/board/__init__.py`
- **MonkeyPatch** (7 connections)
- **BoardUpstreamError** (6 connections) — `src/hal0/board/__init__.py`
- **.request_json()** (5 connections) — `src/hal0/board/__init__.py`
- **test_env_pin_skips_html_harvest()** (5 connections) — `tests/board/test_board_client.py`
- **test_harvested_token_is_cached()** (5 connections) — `tests/board/test_board_client.py`
- **test_harvests_token_from_dashboard_html()** (5 connections) — `tests/board/test_board_client.py`
- **test_upstream_4xx_passes_status_through()** (5 connections) — `tests/board/test_board_client.py`
- **test_upstream_5xx_maps_to_502()** (5 connections) — `tests/board/test_board_client.py`
- **BoardUnreachable** (4 connections) — `src/hal0/board/__init__.py`
- **_default_agent_id()** (4 connections) — `src/hal0/board/__init__.py`
- **._current_token()** (4 connections) — `src/hal0/board/__init__.py`
- **.from_env()** (4 connections) — `src/hal0/board/__init__.py`
- **.handler()** (4 connections) — `tests/board/test_board_client.py`
- **.respond()** (4 connections) — `tests/board/test_board_client.py`
- **.respond_text()** (4 connections) — `tests/board/test_board_client.py`
- **test_401_reharvests_token_and_retries()** (4 connections) — `tests/board/test_board_client.py`
- **test_default_agent_id_from_env()** (4 connections) — `tests/board/test_board_client.py`
- **test_empty_200_body_returns_empty_dict()** (4 connections) — `tests/board/test_board_client.py`
- **test_transport_failure_maps_to_503()** (4 connections) — `tests/board/test_board_client.py`
- **._fetch_html_token()** (3 connections) — `src/hal0/board/__init__.py`
- *... and 31 more nodes in this community*

## Relationships

- [errors.py](errors.py.md) (1 shared connections)
- [_HermesGateway](_HermesGateway.md) (1 shared connections)
- [test_board_chat.py](test_board_chat.py.md) (1 shared connections)
- [test_board_chat_tool_use_e2e.py](test_board_chat_tool_use_e2e.py.md) (1 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/board/__init__.py`
- `tests/board/test_board_client.py`

## Audit Trail

- EXTRACTED: 208 (95%)
- INFERRED: 11 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*