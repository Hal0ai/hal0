# hardware.py

> 63 nodes · cohesion 0.06

## Key Concepts

- **hardware.py** (25 connections) — `src/hal0/api/routes/hardware.py`
- **Any** (19 connections)
- **Request** (14 connections)
- **_cached_snapshot()** (13 connections) — `src/hal0/api/routes/hardware.py`
- **get_hardware()** (9 connections) — `src/hal0/api/routes/hardware.py`
- **_npu_status()** (9 connections) — `src/hal0/api/routes/hardware.py`
- **system_info_endpoint()** (9 connections) — `src/hal0/api/routes/hardware.py`
- **_SnapStub** (9 connections) — `tests/api/test_hardware_routes.py`
- **_local_live_stats()** (8 connections) — `src/hal0/api/routes/hardware.py`
- **stats_hardware()** (8 connections) — `src/hal0/api/routes/hardware.py`
- **_fake_request()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_cached_snapshot_concurrent_stale_polls_no_wedge()** (7 connections) — `tests/api/test_hardware_routes.py`
- **test_cached_snapshot_stale_returns_cached_and_refreshes_in_background()** (7 connections) — `tests/api/test_hardware_routes.py`
- **_gpu_sample()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **_per_slot_memory()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **_proxy_upstream_endpoint()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **_refresh_snapshot_cache()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **reprobe_hardware()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **stats_slots()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **test_cached_snapshot_fresh_hits_no_extra_probe()** (6 connections) — `tests/api/test_hardware_routes.py`
- **_background_revalidate()** (5 connections) — `src/hal0/api/routes/hardware.py`
- **_snapshot_lock()** (5 connections) — `src/hal0/api/routes/hardware.py`
- **test_cached_snapshot_coalesces_concurrent_polls()** (5 connections) — `tests/api/test_hardware_routes.py`
- **_clear_in_flight()** (4 connections) — `src/hal0/api/routes/hardware.py`
- **_local_image_repos()** (4 connections) — `src/hal0/api/routes/hardware.py`
- *... and 38 more nodes in this community*

## Relationships

- [HardwareInfo](HardwareInfo.md) (16 shared connections)
- [sample](sample.md) (5 shared connections)
- [images](images.md) (4 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [npu_occupancy](npu_occupancy.md) (1 shared connections)
- [build_per_slot](build_per_slot.md) (1 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [health.py](health.py.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/hardware.py`
- `tests/api/test_hardware_routes.py`

## Audit Trail

- EXTRACTED: 240 (91%)
- INFERRED: 24 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*