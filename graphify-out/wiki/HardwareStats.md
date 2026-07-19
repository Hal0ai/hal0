# HardwareStats

> 60 nodes · cohesion 0.06

## Key Concepts

- **HardwareStats** (31 connections) — `src/hal0/hardware/stats.py`
- **test_stats.py** (10 connections) — `tests/hardware/test_stats.py`
- **_RunSpy** (10 connections) — `tests/hardware/test_stats.py`
- **test_stats_gpu_view_delegation.py** (7 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **test_gpu_sample_uses_cached_vendor_and_drm()** (7 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **_mk_amd_drm()** (7 connections) — `tests/hardware/test_stats.py`
- **MonkeyPatch** (7 connections)
- **test_amd_gpu_metrics_make_zero_nvidia_smi_calls()** (7 connections) — `tests/hardware/test_stats.py`
- **test_amd_gpu_values_from_sysfs()** (7 connections) — `tests/hardware/test_stats.py`
- **test_snapshot_does_not_probe_slot_ports()** (7 connections) — `tests/hardware/test_stats.py`
- **test_vendor_cached_once()** (7 connections) — `tests/hardware/test_stats.py`
- **._read_text()** (6 connections) — `src/hal0/hardware/stats.py`
- **_mk_amd_drm()** (6 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **test_snapshot_carries_split_and_forced_high_flag()** (6 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **.ram_available_gb()** (5 connections) — `src/hal0/hardware/stats.py`
- **.ram_used_gb()** (5 connections) — `src/hal0/hardware/stats.py`
- **MonkeyPatch** (5 connections)
- **Path** (5 connections)
- **test_gpu_sample_amd_fields()** (5 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **test_snapshot_flag_false_without_forced_high()** (5 connections) — `tests/hardware/test_stats_gpu_view_delegation.py`
- **Path** (5 connections)
- **stats.py** (4 connections) — `src/hal0/hardware/stats.py`
- **.occupied_slot_ports()** (4 connections) — `src/hal0/hardware/stats.py`
- **.slot_port_occupancy()** (4 connections) — `src/hal0/hardware/stats.py`
- **test_nvidia_path_preserved()** (4 connections) — `tests/hardware/test_stats.py`
- *... and 35 more nodes in this community*

## Relationships

- [probe.py](probe.py.md) (13 shared connections)
- [sample](sample.md) (2 shared connections)
- [socket](socket.md) (1 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/stats.py`
- `tests/hardware/test_stats.py`
- `tests/hardware/test_stats_gpu_view_delegation.py`

## Audit Trail

- EXTRACTED: 200 (87%)
- INFERRED: 29 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*