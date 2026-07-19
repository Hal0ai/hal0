# HardwareInfo

> 87 nodes

## Key Concepts

- **HardwareInfo** (60 connections) — `src/hal0/config/schema.py`
- **NPUInfo** (24 connections) — `src/hal0/config/schema.py`
- **test_hardware_routes.py** (24 connections) — `tests/api/test_hardware_routes.py`
- **_flatten_for_ui()** (15 connections) — `src/hal0/api/routes/hardware.py`
- **_clamp_context_size()** (9 connections) — `src/hal0/install/orchestrate.py`
- **_SnapStub** (9 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_strix_halo_is_unified()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_discrete_sample_is_not_uma()** (7 connections) — `tests/api/test_hardware_routes.py`
- **TestHostDetectionInStatsHardware** (7 connections) — `tests/api/test_hardware_routes.py`
- **MonkeyPatch** (7 connections)
- **_fake_request()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_cached_snapshot_stale_returns_cached_and_refreshes_in_background()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_cached_snapshot_concurrent_stale_polls_no_wedge()** (7 connections) — `tests/api/test_hardware_routes.py`
- **_GpuStatsStub** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_without_sample_defaults_to_not_uma()** (6 connections) — `tests/api/test_hardware_routes.py`
- **TestClient** (6 connections)
- **test_cached_snapshot_fresh_hits_no_extra_probe()** (6 connections) — `tests/api/test_hardware_routes.py`
- **test_clamp_context_size_honors_cgroup_cap()** (6 connections) — `tests/install/test_orchestrate.py`
- **test_apply_selections.py** (5 connections) — `tests/api/test_apply_selections.py`
- **_gpu_hardware()** (5 connections) — `tests/api/test_apply_selections.py`
- **test_flatten_pass_through_kvm_with_virtio_gpu()** (5 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_bare_metal_nvidia_promotes_gpu_into_label()** (5 connections) — `tests/api/test_hardware_routes.py`
- **test_flatten_handles_legacy_payload_without_platform()** (5 connections) — `tests/api/test_hardware_routes.py`
- **.test_configured_pass_through_unchanged()** (5 connections) — `tests/api/test_hardware_routes.py`
- **test_cached_snapshot_coalesces_concurrent_polls()** (5 connections) — `tests/api/test_hardware_routes.py`
- *... and 62 more nodes in this community*

## Relationships

- [test_probe.py](test_probe.py.md) (12 shared connections)
- [hardware.py](hardware.py.md) (11 shared connections)
- [sample](sample.md) (9 shared connections)
- [build_auto_selections](build_auto_selections.md) (8 shared connections)
- [ConfigParseError](ConfigParseError.md) (7 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (6 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (6 shared connections)
- [probe.py](probe.py.md) (5 shared connections)
- [load_answers](load_answers.md) (5 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (5 shared connections)
- [suggest_models](suggest_models.md) (4 shared connections)
- [orchestrate.py](orchestrate.py.md) (4 shared connections)

## Source Files

- `src/hal0/api/routes/hardware.py`
- `src/hal0/config/schema.py`
- `src/hal0/hardware/probe.py`
- `src/hal0/install/orchestrate.py`
- `tests/api/test_apply_selections.py`
- `tests/api/test_hardware_routes.py`
- `tests/config/test_schema.py`
- `tests/install/test_orchestrate.py`
- `tests/install/test_profile_derive.py`

## Audit Trail

- EXTRACTED: 267 (70%)
- INFERRED: 115 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*