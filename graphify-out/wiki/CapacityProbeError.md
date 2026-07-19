# CapacityProbeError

> 13 nodes

## Key Concepts

- **CapacityProbeError** (6 connections) — `src/hal0/slots/capacity.py`
- **CapacitySnapshot** (6 connections) — `src/hal0/slots/capacity.py`
- **.probe()** (6 connections) — `src/hal0/slots/capacity.py`
- **.fits()** (5 connections) — `src/hal0/slots/capacity.py`
- **Any** (4 connections)
- **.as_dict()** (3 connections) — `src/hal0/slots/capacity.py`
- **test_profile_fits_slot_matrix()** (2 connections) — `tests/slots/test_model_preferred_profile.py`
- **test_runner_fits_slot_matrix()** (2 connections) — `tests/slots/test_model_preferred_runner.py`
- **/proc/meminfo unreadable, or DRM sysfs not enumerable.** (1 connections) — `src/hal0/slots/capacity.py`
- **Point-in-time view of system and slot capacity.      All memory values are in me** (1 connections) — `src/hal0/slots/capacity.py`
- **Return True if the requested memory would fit within current headroom.** (1 connections) — `src/hal0/slots/capacity.py`
- **Read current system state and return a fresh snapshot.          Args:** (1 connections) — `src/hal0/slots/capacity.py`
- **Serialise to a JSON-safe dict for API responses.** (1 connections) — `src/hal0/slots/capacity.py`

## Relationships

- [capacity.py](capacity.py.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [_read_meminfo](_read_meminfo.md) (2 shared connections)
- [build_per_slot](build_per_slot.md) (1 shared connections)
- [HardwareInfo](HardwareInfo.md) (1 shared connections)
- [test_model_preferred_profile.py](test_model_preferred_profile.py.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `tests/slots/test_model_preferred_profile.py`
- `tests/slots/test_model_preferred_runner.py`

## Audit Trail

- EXTRACTED: 32 (82%)
- INFERRED: 7 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*