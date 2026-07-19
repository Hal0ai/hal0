# ReaperHost

> 51 nodes · cohesion 0.06

## Key Concepts

- **ReaperHost** (18 connections) — `src/hal0/slots/reaper.py`
- **.pressure_evict_once()** (13 connections) — `src/hal0/slots/reaper.py`
- **.sweep_idle_once()** (9 connections) — `src/hal0/slots/reaper.py`
- **test_model_preferred_profile.py** (9 connections) — `tests/slots/test_model_preferred_profile.py`
- **.evict_timeout_for()** (7 connections) — `src/hal0/slots/reaper.py`
- **_register()** (7 connections) — `tests/slots/test_model_preferred_profile.py`
- **CapacityProbeError** (6 connections) — `src/hal0/slots/capacity.py`
- **_read_meminfo()** (6 connections) — `src/hal0/slots/capacity.py`
- **reaper.py** (6 connections) — `src/hal0/slots/reaper.py`
- **_gpu_vulkan_cfg()** (6 connections) — `tests/slots/test_model_preferred_profile.py`
- **.fits()** (5 connections) — `src/hal0/slots/capacity.py`
- **is_pinned()** (5 connections) — `src/hal0/slots/reaper.py`
- **._transition()** (5 connections) — `src/hal0/slots/reaper.py`
- **._loop()** (5 connections) — `src/hal0/slots/reaper.py`
- **.sweep_candidates()** (5 connections) — `src/hal0/slots/reaper.py`
- **probe_host_free_mb()** (4 connections) — `src/hal0/slots/reaper.py`
- **probe_host_total_mb()** (4 connections) — `src/hal0/slots/reaper.py`
- **._current_state()** (4 connections) — `src/hal0/slots/reaper.py`
- **._load_slot_config()** (4 connections) — `src/hal0/slots/reaper.py`
- **.unload()** (4 connections) — `src/hal0/slots/reaper.py`
- **test_apply_preferred_profile_skips_incompatible()** (4 connections) — `tests/slots/test_model_preferred_profile.py`
- **test_apply_preferred_profile_swaps_when_compatible()** (4 connections) — `tests/slots/test_model_preferred_profile.py`
- **test_create_adopts_compatible_preferred_profile()** (4 connections) — `tests/slots/test_model_preferred_profile.py`
- **test_create_ignores_cross_backend_preferred_profile()** (4 connections) — `tests/slots/test_model_preferred_profile.py`
- **test_create_ignores_incompatible_preferred_profile()** (4 connections) — `tests/slots/test_model_preferred_profile.py`
- *... and 26 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (16 shared connections)
- [build_per_slot](build_per_slot.md) (5 shared connections)
- [SlotManager](SlotManager.md) (5 shared connections)
- [SlotState](SlotState.md) (3 shared connections)
- [probe.py](probe.py.md) (2 shared connections)
- [slot](slot.md) (1 shared connections)
- [ModelDefaults](ModelDefaults.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `src/hal0/slots/reaper.py`
- `tests/slots/test_model_preferred_profile.py`
- `tests/slots/test_model_preferred_runner.py`

## Audit Trail

- EXTRACTED: 167 (87%)
- INFERRED: 25 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*