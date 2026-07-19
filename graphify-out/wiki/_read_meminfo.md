# _read_meminfo

> 6 nodes

## Key Concepts

- **_read_meminfo()** (6 connections) — `src/hal0/slots/capacity.py`
- **probe_host_free_mb()** (4 connections) — `src/hal0/slots/reaper.py`
- **probe_host_total_mb()** (4 connections) — `src/hal0/slots/reaper.py`
- **Return (total_mib, available_mib) from /proc/meminfo.      Raises CapacityProbeE** (1 connections) — `src/hal0/slots/capacity.py`
- **Return free host memory in MiB, GTT-aware where possible (§21.10).      Prefers** (1 connections) — `src/hal0/slots/reaper.py`
- **Return total host memory in MiB, from the same GTT-aware source.      Only consu** (1 connections) — `src/hal0/slots/reaper.py`

## Relationships

- [CapacityProbeError](CapacityProbeError.md) (2 shared connections)
- [probe.py](probe.py.md) (2 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [capacity.py](capacity.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `src/hal0/slots/reaper.py`

## Audit Trail

- EXTRACTED: 10 (59%)
- INFERRED: 7 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*