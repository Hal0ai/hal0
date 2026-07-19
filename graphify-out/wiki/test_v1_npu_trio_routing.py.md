# test_v1_npu_trio_routing.py

> 38 nodes

## Key Concepts

- **test_v1_npu_trio_routing.py** (18 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **TestClient** (14 connections)
- **Any** (9 connections)
- **_make_capture_transport()** (8 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **trio_client()** (7 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_npu_skips_trio_when_slot_disabled()** (6 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_stt_npu_skips_trio_when_slot_disabled()** (6 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **_seed_slot_toml()** (5 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **_pin_npu_ready()** (5 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_with_no_npu_slots_configured_skips_trio()** (5 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_npu_routes_to_npu_container_when_dispatchable()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_npu_preserves_request_body_verbatim()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_npu_returns_503_when_npu_not_dispatchable()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_request_for_non_npu_model_skips_trio()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_request_matches_trio_by_slot_name()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_stt_npu_routes_to_npu_container_when_dispatchable()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_stt_request_matches_trio_by_slot_name()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_embed_with_missing_model_skips_trio()** (4 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **seed_npu_trio()** (3 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **test_stt_npu_returns_503_when_npu_not_dispatchable()** (3 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **Path** (1 connections)
- **MockTransport** (1 connections)
- **End-to-end routing tests for the NPU trio dispatch (containerized npu slot).  A** (1 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **Lay down the NPU trio slot TOMLs on disk.      ``flm`` is the containerized anch** (1 connections) — `tests/api/test_v1_npu_trio_routing.py`
- **MockTransport that records every request to the npu container port.** (1 connections) — `tests/api/test_v1_npu_trio_routing.py`
- *... and 13 more nodes in this community*

## Relationships

- [create_app](create_app.md) (6 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `tests/api/test_v1_npu_trio_routing.py`

## Audit Trail

- EXTRACTED: 132 (95%)
- INFERRED: 7 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*