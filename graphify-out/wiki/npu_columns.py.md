# npu_columns.py

> 15 nodes · cohesion 0.19

## Key Concepts

- **npu_columns.py** (7 connections) — `src/hal0/providers/npu_columns.py`
- **read_aie_columns()** (6 connections) — `src/hal0/providers/npu_columns.py`
- **cached_aie_columns()** (5 connections) — `src/hal0/providers/npu_columns.py`
- **_parse_aie_partitions()** (5 connections) — `src/hal0/providers/npu_columns.py`
- **aie_total_columns()** (3 connections) — `src/hal0/providers/npu_columns.py`
- **_container_runtime()** (3 connections) — `src/hal0/providers/npu_columns.py`
- **Any** (3 connections)
- **invalidate_columns_cache()** (2 connections) — `src/hal0/providers/npu_columns.py`
- **AIE column-allocation probe for the live FLM/NPU container — NPU occupancy.  The** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Resolve the podman/docker binary, or ``None`` when neither exists.      Mirrors** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Parse ``xrt-smi examine -r aie-partitions -f JSON`` output.      Contract: ``dev** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Probe live AIE column allocation inside *container_name*.      Execs ``xrt-smi e** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Return AIE columns for *container_name*, cached for the TTL window.      On a ca** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Drop cached column data so the next read re-probes.      With *container_name* c** (1 connections) — `src/hal0/providers/npu_columns.py`
- **Total AIE columns on this host's NPU (the occupancy denominator/cap).      Fallb** (1 connections) — `src/hal0/providers/npu_columns.py`

## Relationships

- [npu_occupancy](npu_occupancy.md) (1 shared connections)

## Source Files

- `src/hal0/providers/npu_columns.py`

## Audit Trail

- EXTRACTED: 40 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*