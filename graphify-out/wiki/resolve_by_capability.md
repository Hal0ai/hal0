# resolve_by_capability

> 76 nodes

## Key Concepts

- **resolve_by_capability()** (33 connections) — `src/hal0/dispatcher/_capability_resolve.py`
- **FakeUpstreamRegistry** (22 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_tts_path_routing.py** (20 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_image_routing.py** (14 connections) — `tests/dispatcher/test_image_routing.py`
- **_registry_with_slots()** (13 connections) — `tests/dispatcher/test_image_routing.py`
- **make_slot()** (11 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **FakeModelRegistry** (10 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_audio_speech_uses_tts_default_from_path()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_audio_speech_kokoro_v1_reaches_tts_slot()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_dispatch_kokoro_v1_resolves_container_remote_tts()** (8 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **make_remote_tts()** (7 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_container_tts_upstream_preempts_registry()** (7 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_resolve_by_capability_chat_default_raises_typed_legacy_error()** (6 connections) — `tests/api/test_v1_chat_slot_alias.py`
- **test_genuine_external_remote_still_rejected()** (6 connections) — `tests/dispatcher/test_image_routing.py`
- **make_request()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_audio_speech_missing_tts_slot_raises_typed_error()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_router_audio_transcriptions_not_default_to_tts()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_genuine_remote_not_accepted_by_path_pin()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **test_proxy_model_name_rule_does_not_resolve_container_remote()** (6 connections) — `tests/dispatcher/test_tts_path_routing.py`
- **_container_remote_img()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_path_without_img_slot_raises_typed_error()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_path_accepts_container_remote()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_image_model_prefix_accepts_container_remote()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_default_for_path_images()** (5 connections) — `tests/dispatcher/test_image_routing.py`
- **test_proxy_audio_speech_path_pins_to_tts()** (5 connections) — `tests/dispatcher/test_tts_path_routing.py`
- *... and 51 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (16 shared connections)
- [Upstream](Upstream.md) (16 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (6 shared connections)
- [UpstreamCall](UpstreamCall.md) (6 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (4 shared connections)
- [test_slot_aliases.py](test_slot_aliases.py.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [test_v1_chat_slot_alias.py](test_v1_chat_slot_alias.py.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/_capability_resolve.py`
- `tests/api/test_v1_chat_slot_alias.py`
- `tests/dispatcher/test_image_routing.py`
- `tests/dispatcher/test_tts_path_routing.py`
- `tests/slots/test_slot_aliases.py`

## Audit Trail

- EXTRACTED: 263 (78%)
- INFERRED: 73 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*