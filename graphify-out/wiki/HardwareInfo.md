# HardwareInfo

> 52 nodes · cohesion 0.07

## Key Concepts

- **HardwareInfo** (60 connections) — `src/hal0/config/schema.py`
- **NPUInfo** (24 connections) — `src/hal0/config/schema.py`
- **test_hardware_routes.py** (24 connections) — `tests/api/test_hardware_routes.py`
- **load_hardware_info()** (16 connections) — `src/hal0/config/loader.py`
- **_flatten_for_ui()** (15 connections) — `src/hal0/api/routes/hardware.py`
- **save_hardware_info()** (7 connections) — `src/hal0/config/loader.py`
- **test_flatten_discrete_sample_is_not_uma()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_strix_halo_is_unified()** (7 connections) — `tests/api/test_hardware_routes.py`
- **TestHardwareJsonRoundTrip** (7 connections) — `tests/config/test_loader.py`
- **test_flatten_without_sample_defaults_to_not_uma()** (6 connections) — `tests/api/test_hardware_routes.py`
- **.test_cgroup_max_mb_round_trips()** (6 connections) — `tests/config/test_loader.py`
- **test_clamp_context_size_honors_cgroup_cap()** (6 connections) — `tests/install/test_orchestrate.py`
- **_platform_label()** (5 connections) — `src/hal0/api/routes/hardware.py`
- **test_apply_selections.py** (5 connections) — `tests/api/test_apply_selections.py`
- **_gpu_hardware()** (5 connections) — `tests/api/test_apply_selections.py`
- **test_flatten_bare_metal_nvidia_promotes_gpu_into_label()** (5 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_handles_legacy_payload_without_platform()** (5 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_pass_through_kvm_with_virtio_gpu()** (5 connections) — `tests/api/test_hardware_routes.py`
- **.test_save_then_load()** (5 connections) — `tests/config/test_loader.py`
- **TestHardwareInfo** (5 connections) — `tests/config/test_schema.py`
- **_FakeProbe** (4 connections) — `tests/api/test_apply_selections.py`
- **_discrete_sample()** (4 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_cgroup_max_mb_none_when_unlimited()** (4 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_includes_cgroup_max_mb_when_set()** (4 connections) — `tests/api/test_hardware_routes.py`
- **.probe_async()** (3 connections) — `src/hal0/hardware/probe.py`
- *... and 27 more nodes in this community*

## Relationships

- [hardware.py](hardware.py.md) (16 shared connections)
- [test_probe.py](test_probe.py.md) (13 shared connections)
- [load_hal0_config](load_hal0_config.md) (8 shared connections)
- [build_auto_selections](build_auto_selections.md) (8 shared connections)
- [sample](sample.md) (7 shared connections)
- [orchestrate.py](orchestrate.py.md) (6 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (6 shared connections)
- [probe.py](probe.py.md) (5 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (5 shared connections)
- [load_answers](load_answers.md) (5 shared connections)
- [schema.py](schema.py.md) (4 shared connections)
- [suggest_models](suggest_models.md) (4 shared connections)

## Source Files

- `src/hal0/api/routes/hardware.py`
- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `src/hal0/hardware/probe.py`
- `tests/api/test_apply_selections.py`
- `tests/api/test_hardware_routes.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema.py`
- `tests/install/test_orchestrate.py`

## Audit Trail

- EXTRACTED: 160 (56%)
- INFERRED: 125 (44%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*