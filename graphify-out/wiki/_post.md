# ._post

> 19 nodes

## Key Concepts

- **._post()** (9 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.dispatch_embed_npu()** (8 connections) — `src/hal0/dispatcher/npu_trio.py`
- **is_container_npu_cfg()** (7 connections) — `src/hal0/dispatcher/_npu_common.py`
- **.dispatch_stt_npu()** (7 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.resolve_npu_url()** (5 connections) — `src/hal0/dispatcher/npu_trio.py`
- **._merge_headers()** (4 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.__init__()** (3 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Any** (3 connections)
- **Response** (3 connections)
- **_npu_common.py** (2 connections) — `src/hal0/dispatcher/_npu_common.py`
- **AsyncClient** (2 connections)
- **Any** (1 connections)
- **Shared NPU-slot helpers for dispatcher modules (Phase A container cutover).** (1 connections) — `src/hal0/dispatcher/_npu_common.py`
- **True when this slot config describes a containerized NPU slot.      Detection: d** (1 connections) — `src/hal0/dispatcher/_npu_common.py`
- **Return the static-port URL for the containerized npu slot, or None.          Ret** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Forward an STT request to the npu container's ``/v1/audio/transcriptions``.** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Forward an embeddings request to the npu container's ``/v1/embeddings``.** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Build the outbound header dict.          We always set ``content-type`` (callers** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Single chokepoint for the FLM POST. Uses the shared client when         provided** (1 connections) — `src/hal0/dispatcher/npu_trio.py`

## Relationships

- [NpuTrioRouter](NpuTrioRouter.md) (8 shared connections)
- [.apply](apply.md) (1 shared connections)
- [test_npu_swap_status.py](test_npu_swap_status.py.md) (1 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)
- [Any](Any.md) (1 shared connections)
- [_fetch_json](_fetch_json.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/_npu_common.py`
- `src/hal0/dispatcher/npu_trio.py`

## Audit Trail

- EXTRACTED: 54 (89%)
- INFERRED: 7 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*