# FakeUpstreamRegistry

> 30 nodes · cohesion 0.12

## Key Concepts

- **FakeUpstreamRegistry** (16 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_rerank_path_routing.py** (14 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **FakeModelRegistry** (10 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_no_rerank_slot_falls_back_to_no_route_found()** (9 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **make_slot()** (8 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_forward_path_rewritten_to_v1_rerank()** (8 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_other_paths_not_rewritten()** (7 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_router_default_for_rerank_path_is_rerank()** (6 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **make_remote_rerank()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **make_request()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_embeddings_still_pin_to_embed()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_proxy_rerankings_path_pin_resolves_container_remote()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_rerank_fragment_no_longer_pins_embed()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **test_rerankings_path_pins_to_rerank_slot()** (5 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **.get()** (3 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **.__init__()** (3 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **.__init__()** (2 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **.route_for()** (2 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **.list()** (2 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **Request** (1 connections)
- **Tests for rerank path-based routing to the ``rerank`` slot.  Phase C task C4 — `** (1 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **/v1/rerankings must NOT pin to embed — only to the rerank slot.      Before Phas** (1 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **/v1/embeddings still routes to embed — Phase C does NOT change embed routing.** (1 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **_default_for_path('/v1/rerankings') returns 'rerank', not 'embed'.** (1 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- **Full dispatch(): outgoing upstream URL uses /v1/rerank, not /v1/rerankings.** (1 connections) — `tests/dispatcher/test_rerank_path_routing.py`
- *... and 5 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (10 shared connections)
- [Upstream](Upstream.md) (9 shared connections)
- [UpstreamCall](UpstreamCall.md) (3 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_rerank_path_routing.py`

## Audit Trail

- EXTRACTED: 114 (87%)
- INFERRED: 17 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*