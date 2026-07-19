# test_probe.py

> 89 nodes

## Key Concepts

- **test_probe.py** (60 connections) — `tests/hardware/test_probe.py`
- **MonkeyPatch** (56 connections)
- **GPUInfo** (49 connections) — `src/hal0/config/schema.py`
- **HardwareProbe** (21 connections) — `src/hal0/hardware/probe.py`
- **Path** (8 connections)
- **_mk_run()** (7 connections) — `tests/hardware/test_probe.py`
- **test_write_atomic()** (7 connections) — `tests/hardware/test_probe.py`
- **_stub_files()** (7 connections) — `tests/hardware/test_probe.py`
- **_stub_probe_env()** (7 connections) — `tests/hardware/test_probe.py`
- **test_probe_assembles_hardware_info()** (6 connections) — `tests/hardware/test_probe.py`
- **test_probe_async()** (5 connections) — `tests/hardware/test_probe.py`
- **test_detect_platform_kvm_via_dmi()** (5 connections) — `tests/hardware/test_probe.py`
- **test_probe_includes_named_gpu_even_when_vendor_unknown()** (5 connections) — `tests/hardware/test_probe.py`
- **test_probe_populates_cgroup_max_mb()** (5 connections) — `tests/hardware/test_probe.py`
- **test_probe_cgroup_max_mb_none_when_unlimited()** (5 connections) — `tests/hardware/test_probe.py`
- **test_detect_amd_via_drm()** (4 connections) — `tests/hardware/test_probe.py`
- **test_detect_gpu_cpu_only()** (4 connections) — `tests/hardware/test_probe.py`
- **test_detect_gpu_first_match_wins()** (4 connections) — `tests/hardware/test_probe.py`
- **test_probe_assembles_unified_memory_on_uma()** (4 connections) — `tests/hardware/test_probe.py`
- **test_detect_platform_wsl_via_proc_version()** (4 connections) — `tests/hardware/test_probe.py`
- **test_detect_platform_lxc()** (4 connections) — `tests/hardware/test_probe.py`
- **test_detect_platform_proxmox_kvm_heuristic()** (4 connections) — `tests/hardware/test_probe.py`
- **test_probe_populates_platform()** (4 connections) — `tests/hardware/test_probe.py`
- **test_probe_validate_npu_records_functional_result()** (4 connections) — `tests/hardware/test_probe.py`
- **test_probe_validate_npu_records_failure()** (4 connections) — `tests/hardware/test_probe.py`
- *... and 64 more nodes in this community*

## Relationships

- [probe.py](probe.py.md) (15 shared connections)
- [HardwareInfo](HardwareInfo.md) (12 shared connections)
- [installer.py](installer.py.md) (5 shared connections)
- [build_auto_selections](build_auto_selections.md) (2 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [schema.py](schema.py.md) (1 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)
- [load_answers](load_answers.md) (1 shared connections)
- [test_emit_answers.py](test_emit_answers.py.md) (1 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (1 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/hardware/probe.py`
- `tests/hardware/test_probe.py`

## Audit Trail

- EXTRACTED: 341 (82%)
- INFERRED: 77 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*