# Mount

> 57 nodes

## Key Concepts

- **Mount** (28 connections) — `src/hal0/providers/base.py`
- **RuntimeLaunchPlan** (28 connections) — `src/hal0/providers/base.py`
- **_render_quadlet_from_plan()** (21 connections) — `src/hal0/providers/container.py`
- **TestMount** (13 connections) — `tests/providers/test_runtime_launch_plan.py`
- **HealthCheck** (10 connections) — `src/hal0/providers/base.py`
- **TestHostNetLoopbackFence** (10 connections) — `tests/providers/test_container.py`
- **TestRenderUnitFromPlan** (10 connections) — `tests/providers/test_runtime_launch_plan.py`
- **base.py** (9 connections) — `src/hal0/providers/base.py`
- **test_runtime_launch_plan.py** (9 connections) — `tests/providers/test_runtime_launch_plan.py`
- **test_render_llama_shim_matches_equivalent_plan()** (9 connections) — `tests/providers/test_runtime_launch_plan.py`
- **_render_from_plan()** (7 connections) — `tests/providers/test_runtime_launch_plan.py`
- **TestHealthCheck** (7 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.coerce()** (6 connections) — `src/hal0/providers/base.py`
- **_exec_tokens()** (6 connections) — `tests/providers/test_container.py`
- **._llama_plan()** (6 connections) — `tests/providers/test_container.py`
- **_render_llama()** (6 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.test_host_net_plan_never_renders_zero_bind()** (5 connections) — `tests/providers/test_container.py`
- **.test_config_default_host_net_flips_bind_for_bridge_plan()** (5 connections) — `tests/providers/test_container.py`
- **.test_bridge_mode_keeps_zero_bind_and_loopback_publish()** (5 connections) — `tests/providers/test_container.py`
- **.test_comfyui_shell_payload_listen_flipped_under_host_net()** (5 connections) — `tests/providers/test_container.py`
- **.test_bridge_mode_publish_host_widen_still_binds_zero()** (4 connections) — `tests/providers/test_container.py`
- **.test_mount_and_legacy_tuple_render_identically()** (4 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.render_quadlet()** (3 connections) — `src/hal0/providers/base.py`
- **_exec()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- **.test_coerce_passes_mount_through()** (3 connections) — `tests/providers/test_runtime_launch_plan.py`
- *... and 32 more nodes in this community*

## Relationships

- [resolve_profile_flags](resolve_profile_flags.md) (11 shared connections)
- [ContainerProvider](ContainerProvider.md) (6 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (5 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (4 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (4 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (4 shared connections)
- [KokoroProvider](KokoroProvider.md) (4 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (3 shared connections)
- [FLMProvider](FLMProvider.md) (3 shared connections)
- [profile.py](profile.py.md) (2 shared connections)
- [Provider](Provider.md) (2 shared connections)
- [store.py](store.py.md) (2 shared connections)

## Source Files

- `src/hal0/providers/base.py`
- `src/hal0/providers/container.py`
- `tests/providers/test_container.py`
- `tests/providers/test_runtime_launch_plan.py`

## Audit Trail

- EXTRACTED: 160 (60%)
- INFERRED: 108 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*