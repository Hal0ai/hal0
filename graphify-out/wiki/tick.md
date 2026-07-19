# .tick

> 13 nodes

## Key Concepts

- **.tick()** (9 connections) — `src/hal0/metrics/sampler.py`
- **sampler.py** (5 connections) — `src/hal0/metrics/sampler.py`
- **_scrape_llama()** (4 connections) — `src/hal0/metrics/sampler.py`
- **.__init__()** (4 connections) — `src/hal0/metrics/sampler.py`
- **_probe_power_snapshot()** (3 connections) — `src/hal0/metrics/sampler.py`
- **GraphEgo()** (3 connections) — `ui/src/dash/memory-graph-ego.jsx`
- **Any** (2 connections)
- **_mb_to_bytes()** (2 connections) — `src/hal0/metrics/sampler.py`
- **memory-graph-ego.jsx** (2 connections) — `ui/src/dash/memory-graph-ego.jsx`
- **SlotManager** (1 connections)
- **T2 per-slot sampler -- background asyncio task, one tick per interval.  Reuses t** (1 connections) — `src/hal0/metrics/sampler.py`
- **Reuse the existing per-slot llama-server scrape (best-effort, degrades to {}).** (1 connections) — `src/hal0/metrics/sampler.py`
- **Run exactly one sample cycle. Exposed directly for tests.** (1 connections) — `src/hal0/metrics/sampler.py`

## Relationships

- [SlotSampler](SlotSampler.md) (4 shared connections)
- [_probe_power](_probe_power.md) (1 shared connections)
- [MetricsWriter](MetricsWriter.md) (1 shared connections)
- [build_per_slot](build_per_slot.md) (1 shared connections)
- [primitives.jsx](primitives.jsx.md) (1 shared connections)
- [main.tsx](main.tsx.md) (1 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/sampler.py`
- `ui/src/dash/memory-graph-ego.jsx`

## Audit Trail

- EXTRACTED: 30 (79%)
- INFERRED: 8 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*