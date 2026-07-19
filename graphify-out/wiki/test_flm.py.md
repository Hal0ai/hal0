# test_flm.py

> 61 nodes · cohesion 0.04

## Key Concepts

- **test_flm.py** (44 connections) — `tests/providers/test_flm.py`
- **Any** (12 connections)
- **test_build_env_multiplex_flags()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_command_does_not_prefix_binary_path()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_does_not_bind_mount_host_flm_tree()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_ld_library_path_includes_xrt()** (4 connections) — `tests/providers/test_flm.py`
- **_mock_response()** (3 connections) — `tests/providers/test_flm.py`
- **test_build_env_defaults_to_no_multiplex()** (3 connections) — `tests/providers/test_flm.py`
- **test_build_env_renames_to_hal0_namespace()** (3 connections) — `tests/providers/test_flm.py`
- **test_container_spec_passes_multiplex_flags_in_command()** (3 connections) — `tests/providers/test_flm.py`
- **test_container_spec_passes_through_accel_device()** (3 connections) — `tests/providers/test_flm.py`
- **test_health_rejects_empty_models()** (3 connections) — `tests/providers/test_flm.py`
- **test_start_cmd_uses_flm_serve()** (3 connections) — `tests/providers/test_flm.py`
- **test_verify_embed_exercises_embeddings()** (3 connections) — `tests/providers/test_flm.py`
- **test_verify_inference_falls_back_to_models0_when_expected_absent()** (3 connections) — `tests/providers/test_flm.py`
- **test_verify_inference_rejects_models_ok_but_inference_failing()** (3 connections) — `tests/providers/test_flm.py`
- **test_verify_inference_requires_round_trip()** (3 connections) — `tests/providers/test_flm.py`
- **model_info()** (2 connections) — `tests/providers/test_flm.py`
- **provider()** (2 connections) — `tests/providers/test_flm.py`
- **slot_cfg()** (2 connections) — `tests/providers/test_flm.py`
- **test_async_spawn_demotes_via_setpriv_not_kwargs()** (2 connections) — `tests/providers/test_flm.py`
- **test_async_spawn_passes_through_when_not_root()** (2 connections) — `tests/providers/test_flm.py`
- **test_flm_id_to_tag_empty_catalog_returns_none()** (2 connections) — `tests/providers/test_flm.py`
- **test_flm_id_to_tag_resolves_colon_tag()** (2 connections) — `tests/providers/test_flm.py`
- **test_flm_validate_false_on_nonzero_rc()** (2 connections) — `tests/providers/test_flm.py`
- *... and 36 more nodes in this community*

## Relationships

- [FLMProvider](FLMProvider.md) (17 shared connections)
- [test_health_is_cheap_and_never_probes_the_npu](test_health_is_cheap_and_never_probes_the_npu.md) (1 shared connections)
- [test_image_ref_honors_slot_override](test_image_ref_honors_slot_override.md) (1 shared connections)
- [test_image_ref_is_hal0ai_flm](test_image_ref_is_hal0ai_flm.md) (1 shared connections)
- [FLMInferError](FLMInferError.md) (1 shared connections)
- [test_verify_inference_probes_expected_model_not_models0](test_verify_inference_probes_expected_model_not_models0.md) (1 shared connections)
- [test_verify_inference_rejects_empty_models](test_verify_inference_rejects_empty_models.md) (1 shared connections)
- [test_verify_inference_rejects_response_with_no_choices](test_verify_inference_rejects_response_with_no_choices.md) (1 shared connections)

## Source Files

- `tests/providers/test_flm.py`

## Audit Trail

- EXTRACTED: 166 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*