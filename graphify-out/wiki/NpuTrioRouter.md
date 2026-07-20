# NpuTrioRouter

> 16 nodes · cohesion 0.22

## Key Concepts

- **NpuTrioRouter** (29 connections) — `src/hal0/dispatcher/npu_trio.py`
- **._post()** (9 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.dispatch_embed_npu()** (8 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.dispatch_stt_npu()** (7 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.resolve_npu_url()** (5 connections) — `src/hal0/dispatcher/npu_trio.py`
- **._merge_headers()** (4 connections) — `src/hal0/dispatcher/npu_trio.py`
- **.__init__()** (3 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Any** (3 connections)
- **Response** (3 connections)
- **AsyncClient** (2 connections)
- **Forward an STT request to the npu container's ``/v1/audio/transcriptions``.** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Forward an embeddings request to the npu container's ``/v1/embeddings``.** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Build the outbound header dict.          We always set ``content-type`` (callers** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Single chokepoint for the FLM POST. Uses the shared client when         provided** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Resolves the npu container's static port and dispatches STT/embed to it.      He** (1 connections) — `src/hal0/dispatcher/npu_trio.py`
- **Return the static-port URL for the containerized npu slot, or None.          Ret** (1 connections) — `src/hal0/dispatcher/npu_trio.py`

## Relationships

- [test_npu_trio.py](test_npu_trio.py.md) (8 shared connections)
- [NpuTrioNotAvailable](NpuTrioNotAvailable.md) (5 shared connections)
- [_mock_transport](_mock_transport.md) (5 shared connections)
- [test_dispatch_stt_raises_when_no_container_npu_slot](test_dispatch_stt_raises_when_no_container_npu_slot.md) (2 shared connections)
- [test_get_config_raises_resolves_none](test_get_config_raises_resolves_none.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [test_is_ready_for_dispatch_raises_resolves_none](test_is_ready_for_dispatch_raises_resolves_none.md) (1 shared connections)
- [Any](Any.md) (1 shared connections)
- [comfyui.py](comfyui.py.md) (1 shared connections)
- [is_container_npu_cfg](is_container_npu_cfg.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_trio.py`

## Audit Trail

- EXTRACTED: 55 (70%)
- INFERRED: 24 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*