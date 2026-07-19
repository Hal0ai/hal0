# _llama_launch_plan

> 38 nodes · cohesion 0.09

## Key Concepts

- **_llama_launch_plan()** (26 connections) — `src/hal0/providers/container.py`
- **test_container_assembler.py** (19 connections) — `tests/providers/test_container_assembler.py`
- **_gpu_profile()** (7 connections) — `tests/providers/test_container_assembler.py`
- **TestLlamaLaunchPlanChatTemplate** (6 connections) — `tests/providers/test_container_chat_template.py`
- **_parval()** (5 connections) — `tests/providers/test_container_assembler.py`
- **test_preview_equals_launch_full_slot()** (5 connections) — `tests/providers/test_container_assembler.py`
- **TestLlamaLaunchPlanMmproj** (5 connections) — `tests/providers/test_container_mmproj.py`
- **_render_from_plan()** (4 connections) — `tests/providers/test_container_assembler.py`
- **test_ngl_in_extra_args_is_denied()** (4 connections) — `tests/providers/test_container_assembler.py`
- **test_server_env_threads_into_plan_and_unit()** (4 connections) — `tests/providers/test_container_assembler.py`
- **.test_chat_template_flag_present_when_path_set()** (4 connections) — `tests/providers/test_container_chat_template.py`
- **_ngl()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_cpu_profile_gets_no_gpu_plumbing()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_gpu_profile_existence_filters_devices()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_ngl_precedence_model_default_beats_profile()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_ngl_precedence_slot_beats_model_default()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_parallel_extra_args_wins_over_slot()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_parallel_none_inherits_profile()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_parallel_one_emits_no_kv_unified()** (3 connections) — `tests/providers/test_container_assembler.py`
- **test_parallel_slot_overrides_profile_and_adds_kv_unified()** (3 connections) — `tests/providers/test_container_assembler.py`
- **.test_chat_template_flag_absent_when_no_path()** (3 connections) — `tests/providers/test_container_chat_template.py`
- **.test_chat_template_override_in_extra_args_wins()** (3 connections) — `tests/providers/test_container_chat_template.py`
- **.test_mmproj_override_in_extra_args_wins()** (3 connections) — `tests/providers/test_container_mmproj.py`
- **test_env_empty_when_no_server_env()** (2 connections) — `tests/providers/test_container_assembler.py`
- **test_model_default_extra_args_emitted_and_overridable()** (2 connections) — `tests/providers/test_container_assembler.py`
- *... and 13 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (6 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (5 shared connections)
- [Mount](Mount.md) (4 shared connections)
- [ProfileConfig](ProfileConfig.md) (2 shared connections)
- [model_store_root](model_store_root.md) (2 shared connections)
- [paths.py](paths.py.md) (1 shared connections)
- [resolve_argv](resolve_argv.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [test_container_mmproj.py](test_container_mmproj.py.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_container_assembler.py`
- `tests/providers/test_container_chat_template.py`
- `tests/providers/test_container_mmproj.py`

## Audit Trail

- EXTRACTED: 99 (69%)
- INFERRED: 45 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*