# ContainerProvider

> God node · 113 connections · `src/hal0/providers/container.py`

**Community:** [ContainerProvider](ContainerProvider.md)

## Connections by Relation

### calls
- _build_spec() `INFERRED`
- rerender_slot_units() `INFERRED`
- ._spec() `EXTRACTED`
- _build_spec() `INFERRED`
- _build_spec() `INFERRED`
- ._load() `INFERRED`
- test_health_404_v1_models_fallback_unchanged() `INFERRED`
- test_gpu_slot_unaffected_still_takes_llama_path() `INFERRED`
- .test_install_and_update_render_byte_identical_units() `EXTRACTED`
- test_preview_equals_launch_full_slot() `INFERRED`
- test_health_200_json_without_model_loaded_stays_ok() `INFERRED`
- test_health_200_model_loaded_ok() `INFERRED`
- test_health_200_model_loading_not_ok() `INFERRED`
- test_health_200_non_json_body_stays_ok() `INFERRED`
- .test_gpu_slot_unaffected_by_npu_branch() `INFERRED`
- .test_load_sync_advertises_model_id_alias() `EXTRACTED`
- .test_load_sync_threads_ctx_size_and_extra_args() `EXTRACTED`
- test_image_present_returns_false_on_nonzero_exit() `INFERRED`
- test_image_present_returns_true_on_zero_exit() `INFERRED`
- test_pull_image_stream_completed_on_success() `INFERRED`

### contains
- container.py `EXTRACTED`

### indirect_call
- container_stub() `INFERRED`

### method
- .container_spec() `EXTRACTED`
- ._render_quadlet_text() `EXTRACTED`
- ._unit_path() `EXTRACTED`
- .load_sync() `EXTRACTED`
- ._run() `EXTRACTED`
- .unload_sync() `EXTRACTED`
- .health() `EXTRACTED`
- .rerender_unit_sync() `EXTRACTED`
- ._unit_name() `EXTRACTED`
- ._write_and_start_unit() `EXTRACTED`
- .expected_argv() `EXTRACTED`
- .is_active() `EXTRACTED`
- .running_argv() `EXTRACTED`
- .running_image() `EXTRACTED`
- .build_env() `EXTRACTED`
- .daemon_reload() `EXTRACTED`
- .image_present() `EXTRACTED`
- .infer() `EXTRACTED`
- .pull_image_stream() `EXTRACTED`
- .wait_ready() `EXTRACTED`

### rationale_for
- Podman-container-per-slot inference backend.      One instance is shared across `EXTRACTED`

### references
- container_provider() `EXTRACTED`
- ._provider() `EXTRACTED`
- _make_provider_with_tmp_unit() `EXTRACTED`

### uses
- [FLMProvider](FLMProvider.md) `INFERRED`
- Updater `INFERRED`
- [Mount](Mount.md) `INFERRED`
- RuntimeLaunchPlan `INFERRED`
- [KokoroProvider](KokoroProvider.md) `INFERRED`
- TestRenderUnit `INFERRED`
- [Provider](Provider.md) `INFERRED`
- [Qwen3TTSProvider](Qwen3TTSProvider.md) `INFERRED`
- UpdateError `INFERRED`
- TestContainerSpec `INFERRED`
- TestLoadSync `INFERRED`
- TestRenderUnitFromSpec `INFERRED`
- UpdateManifestInvalid `INFERRED`
- HealthCheck `INFERRED`
- TestContextSizeDerive `INFERRED`
- [TestFamilyDefaults](TestFamilyDefaults.md) `INFERRED`
- TestHostNetLoopbackFence `INFERRED`
- TestContainerSpecChatTemplate `INFERRED`
- [TestContainerRuntimeProbe](TestContainerRuntimeProbe.md) `INFERRED`
- [TestImageMismatch](TestImageMismatch.md) `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*