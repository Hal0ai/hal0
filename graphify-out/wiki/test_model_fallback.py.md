# test_model_fallback.py

> 43 nodes · cohesion 0.11

## Key Concepts

- **test_model_fallback.py** (25 connections) — `tests/slots/test_model_fallback.py`
- **_add_model()** (19 connections) — `tests/slots/test_model_fallback.py`
- **_mgr()** (16 connections) — `tests/slots/test_model_fallback.py`
- **_llm_cfg()** (14 connections) — `tests/slots/test_model_fallback.py`
- **fallback_local_model()** (13 connections) — `src/hal0/registry/fallback.py`
- **resolve_servable_model()** (8 connections) — `src/hal0/registry/fallback.py`
- **fallback.py** (7 connections) — `src/hal0/registry/fallback.py`
- **looks_diffusion_or_nontext()** (5 connections) — `src/hal0/registry/fallback.py`
- **default_model_cache_check()** (4 connections) — `src/hal0/registry/fallback.py`
- **id_tokens()** (4 connections) — `src/hal0/registry/fallback.py`
- **test_excludes_safetensors_diffusion_checkpoint()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_fallback_picks_largest_on_disk()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_fallback_skips_registered_models_with_missing_file()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_falls_back_to_local_when_configured_id_is_ghost()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_ghost_chat_slot_never_picks_video_model()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_ghost_chat_slot_with_only_video_model_does_not_fall_back()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_name_similarity_beats_larger_unrelated_chat_model()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_no_fallback_for_npu_device_flm_tag()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_no_fallback_when_configured_id_is_curated_pullable()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_no_fallback_when_configured_model_is_local()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_no_fallback_when_no_capability_match()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_size_tiebreak_when_no_name_similarity()** (4 connections) — `tests/slots/test_model_fallback.py`
- **test_excludes_image_capability_model()** (3 connections) — `tests/slots/test_model_fallback.py`
- **test_fallback_default_of_other_capability_not_honoured()** (3 connections) — `tests/slots/test_model_fallback.py`
- **test_fallback_heuristic_unchanged_when_no_default_set()** (3 connections) — `tests/slots/test_model_fallback.py`
- *... and 18 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [get_curated](get_curated.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/registry/fallback.py`
- `tests/slots/test_model_fallback.py`

## Audit Trail

- EXTRACTED: 182 (91%)
- INFERRED: 17 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*