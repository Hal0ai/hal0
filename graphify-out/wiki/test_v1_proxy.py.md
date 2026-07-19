# test_v1_proxy.py

> 22 nodes · cohesion 0.14

## Key Concepts

- **test_v1_proxy.py** (14 connections) — `tests/api/test_v1_proxy.py`
- **TestClient** (13 connections)
- **test_v1_chat_completions_get_is_not_routed()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_chat_completions_no_route_returns_typed_404()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_health_is_unrouted_404()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_load_post_is_unrouted()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_models_handled_by_aggregator()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_rerank_alias_is_routed_not_405()** (3 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_arbitrary_unknown_path_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_completions_no_route_returns_typed_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_embeddings_get_is_not_routed()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_embeddings_no_route_returns_typed_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_rerankings_no_route_returns_typed_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_stats_is_unrouted_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **test_v1_system_info_is_unrouted_404()** (2 connections) — `tests/api/test_v1_proxy.py`
- **Tests for the /v1 surface after the catch-all proxy removal (epic #687).  The ``** (1 connections) — `tests/api/test_v1_proxy.py`
- **/v1/rerank (llama-server's / Jina-style clients' path) must reach the     dispat** (1 connections) — `tests/api/test_v1_proxy.py`
- **GET /v1/models stays on the aggregator and returns the OpenAI shape.** (1 connections) — `tests/api/test_v1_proxy.py`
- **GET /v1/health was a proxy-only path — it now 404s locally.** (1 connections) — `tests/api/test_v1_proxy.py`
- **POST /v1/load was the upstream admin surface — gone with the proxy.      The das** (1 connections) — `tests/api/test_v1_proxy.py`
- **GET on the POST-only chat route is a local routing miss, not a     proxy hop (th** (1 connections) — `tests/api/test_v1_proxy.py`
- **POST /v1/chat/completions for a model no upstream serves → 404     ``dispatch.no** (1 connections) — `tests/api/test_v1_proxy.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_v1_proxy.py`

## Audit Trail

- EXTRACTED: 66 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*