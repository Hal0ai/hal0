# NpuTrioRouter

> 53 nodes

## Key Concepts

- **NpuTrioRouter** (29 connections) — `src/hal0/dispatcher/npu_trio.py`
- **test_npu_trio.py** (24 connections) — `tests/dispatcher/test_npu_trio.py`
- **_slot_manager_with_container_npu()** (17 connections) — `tests/dispatcher/test_npu_trio.py`
- **_mock_transport()** (9 connections) — `tests/dispatcher/test_npu_trio.py`
- **NpuTrioNotAvailable** (8 connections) — `src/hal0/dispatcher/npu_trio.py`
- **test_dispatch_stt_posts_multipart_to_static_port()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_stt_raises_trio_unavailable_when_not_dispatchable()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_stt_raises_when_no_container_npu_slot()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_preserves_extra_params()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_propagates_upstream_status_codes()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_stt_extra_headers_dont_clobber_content_type()** (5 connections) — `tests/dispatcher/test_npu_trio.py`
- **npu_trio.py** (4 connections) — `src/hal0/dispatcher/npu_trio.py`
- **_slot_manager_with_noncontainer_npu()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_container_npu_via_runtime_field_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_serving_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_idle_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_non_ready_container_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_disabled_container_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_noncontainer_npu_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_missing_port_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_posts_json_to_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_dispatch_embed_raises_trio_unavailable()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_offline_container_resolves_none()** (3 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_no_slot_manager_resolves_none()** (3 connections) — `tests/dispatcher/test_npu_trio.py`
- *... and 28 more nodes in this community*

## Relationships

- [._post](_post.md) (8 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_trio.py`
- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 156 (77%)
- INFERRED: 47 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*