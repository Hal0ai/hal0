# Upstream

> 86 nodes · cohesion 0.05

## Key Concepts

- **Upstream** (81 connections) — `src/hal0/upstreams/registry.py`
- **resolve_by_capability()** (33 connections) — `src/hal0/dispatcher/_capability_resolve.py`
- **FakeUpstreamRegistry** (22 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_tts_path_routing.py** (20 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_image_routing.py** (14 connections) — `tests/dispatcher/test_image_routing.py`
- **_registry_with_slots()** (13 connections) — `tests/dispatcher/test_image_routing.py`
- **make_slot()** (11 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **FakeModelRegistry** (10 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_audio_speech_kokoro_v1_reaches_tts_slot()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_audio_speech_uses_tts_default_from_path()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_dispatch_kokoro_v1_resolves_container_remote_tts()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **make_remote_tts()** (7 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_container_tts_upstream_preempts_registry()** (7 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_resolve_by_capability_chat_default_raises_typed_legacy_error()** (6 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_genuine_external_remote_still_rejected()** (6 connections) — `tests/dispatcher/test_image_routing.py`
- **make_request()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_audio_speech_missing_tts_slot_raises_typed_error()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_genuine_remote_not_accepted_by_path_pin()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_model_name_rule_does_not_resolve_container_remote()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_router_audio_transcriptions_not_default_to_tts()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **_container_remote_img()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_default_for_path_images()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_model_prefix_accepts_container_remote()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_path_accepts_container_remote()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_path_without_img_slot_raises_typed_error()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- *... and 61 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (27 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (26 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (9 shared connections)
- [_refresh_model_cache_on_ready](_refresh_model_cache_on_ready.md) (5 shared connections)
- [UpstreamCall](UpstreamCall.md) (5 shared connections)
- [test_slot_aliases.py](test_slot_aliases.py.md) (4 shared connections)
- [router.py](router.py.md) (4 shared connections)
- [SlotLoading](SlotLoading.md) (3 shared connections)
- [test_v1_audio.py](test_v1_audio.py.md) (3 shared connections)
- [test_models_routes.py](test_models_routes.py.md) (2 shared connections)
- [FastAPI](FastAPI.md) (2 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (2 shared connections)

## Source Files

- `src/hal0/dispatcher/_capability_resolve.py`
- `src/hal0/upstreams/registry.py`
- `tests/api/test_v1_chat_slot_alias.py`
- `tests/dispatcher/test_image_routing.py`
- `tests/dispatcher/test_router.py`
- `tests/dispatcher/test_tts_path_routing.py`
- `tests/slots/test_slot_aliases.py`

## Audit Trail

- EXTRACTED: 340 (78%)
- INFERRED: 94 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*