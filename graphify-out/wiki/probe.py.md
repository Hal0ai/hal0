# probe.py

> 92 nodes · cohesion 0.04

## Key Concepts

- **probe.py** (40 connections) — `src/hal0/hardware/probe.py`
- **.probe()** (20 connections) — `src/hal0/hardware/probe.py`
- **Path** (20 connections)
- **_read_text()** (17 connections) — `src/hal0/hardware/probe.py`
- **_run()** (13 connections) — `src/hal0/hardware/probe.py`
- **.gpu_sample()** (11 connections) — `src/hal0/hardware/stats.py`
- **_detect_platform()** (10 connections) — `src/hal0/hardware/probe.py`
- **.snapshot()** (9 connections) — `src/hal0/hardware/stats.py`
- **_detect_amd_gpus()** (8 connections) — `src/hal0/hardware/probe.py`
- **_detect_gpus()** (8 connections) — `src/hal0/hardware/probe.py`
- **_amd_gpu_info()** (7 connections) — `src/hal0/hardware/probe.py`
- **_detect_gpu()** (7 connections) — `src/hal0/hardware/probe.py`
- **_detect_npu()** (7 connections) — `src/hal0/hardware/probe.py`
- **._vendor()** (7 connections) — `src/hal0/hardware/stats.py`
- **_amd_drm_device()** (6 connections) — `src/hal0/hardware/probe.py`
- **_detect_aie_columns()** (6 connections) — `src/hal0/hardware/probe.py`
- **_detect_lspci_fallback()** (6 connections) — `src/hal0/hardware/probe.py`
- **_detect_nvidia_gpus()** (6 connections) — `src/hal0/hardware/probe.py`
- **_detect_vulkan_fallback()** (6 connections) — `src/hal0/hardware/probe.py`
- **.write()** (6 connections) — `src/hal0/hardware/probe.py`
- **_read_sysfs_mb()** (6 connections) — `src/hal0/hardware/probe.py`
- **.gpu_clock_mhz()** (6 connections) — `src/hal0/hardware/stats.py`
- **.gpu_temp_c()** (6 connections) — `src/hal0/hardware/stats.py`
- **_amd_drm_devices()** (5 connections) — `src/hal0/hardware/probe.py`
- **_derive_unified_memory_mb()** (5 connections) — `src/hal0/hardware/probe.py`
- *... and 67 more nodes in this community*

## Relationships

- [test_probe.py](test_probe.py.md) (15 shared connections)
- [HardwareStats](HardwareStats.md) (13 shared connections)
- [HardwareInfo](HardwareInfo.md) (5 shared connections)
- [sample](sample.md) (3 shared connections)
- [ReaperHost](ReaperHost.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [ensure_gateway_api_server_key](ensure_gateway_api_server_key.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/probe.py`
- `src/hal0/hardware/stats.py`

## Audit Trail

- EXTRACTED: 351 (92%)
- INFERRED: 32 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*