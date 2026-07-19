# _spec_provider_for

> 29 nodes

## Key Concepts

- **_spec_provider_for()** (28 connections) — `src/hal0/providers/container.py`
- **test_container_spec_dispatch.py** (13 connections) — `tests/providers/test_container_spec_dispatch.py`
- **TestSpecProviderRuntimeFamily** (11 connections) — `tests/providers/test_runtime_launch_plan.py`
- **test_gpu_slot_unaffected_still_takes_llama_path()** (6 connections) — `tests/providers/test_container_spec_dispatch.py`
- **_exec_argv()** (5 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_tts_kokoro_slot_renders_spec_unit()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_tts_slot_by_type_only_no_profile()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_npu_wins_over_tts_type()** (4 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_npu_returns_flm()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_tts_type_returns_kokoro()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_kokoro_profile_returns_kokoro()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_comfyui_returns_comfyui()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_kokoro_path_does_not_require_registry_model_path()** (3 connections) — `tests/providers/test_container_spec_dispatch.py`
- **.test_kokoro_profile_routes_by_family()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.test_comfyui_profile_routes_by_family()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.test_flm_profile_routes_by_family()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.test_unknown_profile_falls_back_to_device_hint()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- **test_spec_provider_gpu_returns_none()** (2 connections) — `tests/providers/test_container_spec_dispatch.py`
- **test_spec_provider_vulkan_returns_none()** (2 connections) — `tests/providers/test_container_spec_dispatch.py`
- **.test_gpu_profile_routes_to_llama_default()** (2 connections) — `tests/providers/test_runtime_launch_plan.py`
- **Provider for a slot, or None for the GPU/llama-server default.      The runtime** (1 connections) — `src/hal0/providers/container.py`
- **Any** (1 connections)
- **load_sync routes slots to their spec provider (FLM/NPU, Kokoro/TTS).** (1 connections) — `tests/providers/test_container_spec_dispatch.py`
- **The in-container argv from the Quadlet ``Exec=`` key (post P3-quadlet).      Was** (1 connections) — `tests/providers/test_container_spec_dispatch.py`
- **TTS/kokoro slot: spec unit rendered with --model_path, no AddDevice=, correct Pu** (1 connections) — `tests/providers/test_container_spec_dispatch.py`
- *... and 4 more nodes in this community*

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (6 shared connections)
- [FLMProvider](FLMProvider.md) (6 shared connections)
- [ContainerProvider](ContainerProvider.md) (5 shared connections)
- [KokoroProvider](KokoroProvider.md) (5 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (4 shared connections)
- [Mount](Mount.md) (4 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (3 shared connections)
- [SlotState](SlotState.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_container_spec_dispatch.py`
- `tests/providers/test_runtime_launch_plan.py`

## Audit Trail

- EXTRACTED: 66 (56%)
- INFERRED: 51 (44%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*