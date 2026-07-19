# test_hf_routes.py

> 34 nodes

## Key Concepts

- **test_hf_routes.py** (16 connections) — `tests/api/test_hf_routes.py`
- **_hf_search_handler()** (13 connections) — `tests/api/test_hf_routes.py`
- **_patch_httpx_transport()** (13 connections) — `tests/api/test_hf_routes.py`
- **MonkeyPatch** (11 connections)
- **TestClient** (11 connections)
- **test_hf_search_returns_normalised_results()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_forwards_query_param()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_forwards_type_filter()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_caps_results()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_returns_empty_on_transport_error()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_returns_empty_on_upstream_5xx()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_skips_non_dict_entries()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_forwards_hf_token()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_no_token_header_when_unset()** (6 connections) — `tests/api/test_hf_routes.py`
- **test_hf_search_empty_query_returns_empty_without_calling_hf()** (5 connections) — `tests/api/test_hf_routes.py`
- **hf_app()** (4 connections) — `tests/api/test_hf_routes.py`
- **FastAPI** (3 connections)
- **hf_client()** (3 connections) — `tests/api/test_hf_routes.py`
- **Any** (1 connections)
- **Exception** (1 connections)
- **Tests for ``GET /api/hf/search`` (issue #311).  Proxies HuggingFace's public mod** (1 connections) — `tests/api/test_hf_routes.py`
- **Build a MockTransport handler that records the request it served.      ``body``** (1 connections) — `tests/api/test_hf_routes.py`
- **Swap the route's ``httpx.AsyncClient`` for one wired to a MockTransport.      Th** (1 connections) — `tests/api/test_hf_routes.py`
- **Fresh app with the route-level search cache cleared.** (1 connections) — `tests/api/test_hf_routes.py`
- **Mocked HF payload is projected onto the dashboard's row shape.** (1 connections) — `tests/api/test_hf_routes.py`
- *... and 9 more nodes in this community*

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_hf_routes.py`

## Audit Trail

- EXTRACTED: 148 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*