# _spec_provider_for

> 62 nodes · cohesion 0.05

## Key Concepts

- **_spec_provider_for()** (28 connections) — `src/hal0/providers/container.py`
- **KokoroProvider** (26 connections) — `src/hal0/providers/kokoro.py`
- **test_container_spec_dispatch.py** (13 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_kokoro_container_spec.py** (12 connections) — `tests/providers/test_kokoro_container_spec.py`
- **TestSpecProviderRuntimeFamily** (11 connections) — `tests/providers/test_runtime_launch_plan.py`
- **_slot_cfg()** (9 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.container_spec()** (7 connections) — `src/hal0/providers/kokoro.py`
- **test_gpu_slot_unaffected_still_takes_llama_path()** (6 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_renderer_no_device_args_publish_volume_command()** (6 connections) — `tests/providers/test_kokoro_container_spec.py`
- **Any** (5 connections)
- **_exec_argv()** (5 connections) — `tests/providers/test_container_spec_dispatch.py`
- **_render_from_spec()** (5 connections) — `tests/providers/test_kokoro_container_spec.py`
- **test_slot_port_override_wins()** (5 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.infer()** (4 connections) — `src/hal0/providers/kokoro.py`
- **test_npu_wins_over_tts_type()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_tts_kokoro_slot_renders_spec_unit()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_tts_slot_by_type_only_no_profile()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_ro_mount_is_read_only()** (4 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.build_env()** (3 connections) — `src/hal0/providers/kokoro.py`
- **.health()** (3 connections) — `src/hal0/providers/kokoro.py`
- **test_kokoro_path_does_not_require_registry_model_path()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_comfyui_returns_comfyui()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_kokoro_profile_returns_kokoro()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_npu_returns_flm()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_tts_type_returns_kokoro()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- *... and 37 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (8 shared connections)
- [Mount](Mount.md) (8 shared connections)
- [FLMProvider](FLMProvider.md) (6 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (4 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (4 shared connections)
- [Provider](Provider.md) (3 shared connections)
- [get_runner](get_runner.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `src/hal0/providers/kokoro.py`
- `tests/providers/test_container_spec_dispatch.py`
- `tests/providers/test_kokoro_container_spec.py`
- `tests/providers/test_runtime_launch_plan.py`

## Audit Trail

- EXTRACTED: 159 (67%)
- INFERRED: 79 (33%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*