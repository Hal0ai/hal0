# Qwen3TTSProvider

> 46 nodes

## Key Concepts

- **Qwen3TTSProvider** (23 connections) — `src/hal0/providers/qwen3tts.py`
- **test_qwen3tts_container_spec.py** (16 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **_slot_cfg()** (12 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **.container_spec()** (11 connections) — `src/hal0/providers/qwen3tts.py`
- **qwen3tts.py** (6 connections) — `src/hal0/providers/qwen3tts.py`
- **Qwen3TTSInferError** (6 connections) — `src/hal0/providers/qwen3tts.py`
- **Qwen3TTSHealthError** (5 connections) — `src/hal0/providers/qwen3tts.py`
- **Any** (5 connections)
- **test_spec_provider_qwen3tts_profile_returns_qwen3tts()** (5 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_qwen3tts_family_wins_over_generic_tts_fallback()** (5 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **.infer()** (4 connections) — `src/hal0/providers/qwen3tts.py`
- **_render_from_spec()** (4 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_no_registry_model_path_required()** (4 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_renderer_emits_gpu_args_cache_volume_and_miopen_env()** (4 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **_cache_dir()** (3 connections) — `src/hal0/providers/qwen3tts.py`
- **.build_env()** (3 connections) — `src/hal0/providers/qwen3tts.py`
- **.health()** (3 connections) — `src/hal0/providers/qwen3tts.py`
- **_stub_gpu()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_spec_emits_gpu_devices_and_groups()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_spec_command_carries_port_host_model_path_and_voice()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_spec_miopen_env_set_no_hsa_override()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_spec_mounts_ro_model_store_and_rw_cache()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_spec_security_opts_and_loopback_publish()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_cache_dir_env_override()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- **test_slot_port_override_wins()** (3 connections) — `tests/providers/test_qwen3tts_container_spec.py`
- *... and 21 more nodes in this community*

## Relationships

- [Mount](Mount.md) (5 shared connections)
- [Provider](Provider.md) (3 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (3 shared connections)
- [get_runner](get_runner.md) (3 shared connections)
- [Hal0Error](Hal0Error.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [resolve_gpu_device_paths](resolve_gpu_device_paths.md) (1 shared connections)
- [resolve_gpu_group_ids](resolve_gpu_group_ids.md) (1 shared connections)
- [KokoroProvider](KokoroProvider.md) (1 shared connections)

## Source Files

- `src/hal0/providers/qwen3tts.py`
- `tests/providers/test_qwen3tts_container_spec.py`

## Audit Trail

- EXTRACTED: 128 (77%)
- INFERRED: 39 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*