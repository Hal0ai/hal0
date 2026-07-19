# _resolve_llama_scalars

> 111 nodes · cohesion 0.04

## Key Concepts

- **_resolve_llama_scalars()** (31 connections) — `src/hal0/providers/container.py`
- **container.py** (30 connections) — `src/hal0/providers/container.py`
- **Any** (30 connections)
- **test_capability_injection.py** (19 connections) — `tests/providers/test_capability_injection.py`
- **_resolve_image_ref()** (16 connections) — `src/hal0/providers/container.py`
- **.container_spec()** (15 connections) — `src/hal0/providers/container.py`
- **test_image_resolution.py** (14 connections) — `tests/providers/test_image_resolution.py`
- **resolved_command_for_slot()** (12 connections) — `src/hal0/providers/container.py`
- **_rocm_profile()** (12 connections) — `tests/providers/test_capability_injection.py`
- **_resolve_slot_argv()** (11 connections) — `src/hal0/providers/container.py`
- **_plain_model()** (11 connections) — `tests/providers/test_capability_injection.py`
- **_llama_argv_segments()** (9 connections) — `src/hal0/providers/container.py`
- **_profile_image_and_flags()** (9 connections) — `src/hal0/providers/container.py`
- **_profile()** (9 connections) — `tests/providers/test_image_resolution.py`
- **test_parallel_batching.py** (9 connections) — `tests/providers/test_parallel_batching.py`
- **_effective_runner()** (8 connections) — `src/hal0/providers/container.py`
- **_preferred_runner_if_fits()** (7 connections) — `src/hal0/providers/container.py`
- **_resolve_profile_or_base()** (7 connections) — `src/hal0/providers/container.py`
- **resolved_argv_detail_for_slot()** (7 connections) — `src/hal0/providers/container.py`
- **test_precedence_chain_family_beats_profile_but_loses_to_model_extra_args()** (7 connections) — `tests/providers/test_capability_injection.py`
- **test_precedence_chain_ngl_slot_beats_everything()** (7 connections) — `tests/providers/test_capability_injection.py`
- **_profile()** (7 connections) — `tests/providers/test_parallel_batching.py`
- **_resolve_context_size()** (6 connections) — `src/hal0/providers/container.py`
- **_extra_args()** (6 connections) — `tests/providers/test_capability_injection.py`
- **test_jinja_never_injected_for_embedding_profile()** (6 connections) — `tests/providers/test_capability_injection.py`
- *... and 86 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (17 shared connections)
- [ProfileConfig](ProfileConfig.md) (9 shared connections)
- [resolve_argv](resolve_argv.md) (6 shared connections)
- [TestFamilyDefaults](TestFamilyDefaults.md) (6 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (5 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (4 shared connections)
- [resolve_gpu_device_paths](resolve_gpu_device_paths.md) (4 shared connections)
- [get_runner](get_runner.md) (4 shared connections)
- [test_mtp_override.py](test_mtp_override.py.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [Mount](Mount.md) (2 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (2 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_capability_injection.py`
- `tests/providers/test_container.py`
- `tests/providers/test_container_resolved_detail.py`
- `tests/providers/test_image_resolution.py`
- `tests/providers/test_parallel_batching.py`

## Audit Trail

- EXTRACTED: 414 (81%)
- INFERRED: 99 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*