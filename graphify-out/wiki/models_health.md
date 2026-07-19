# models_health

> 15 nodes

## Key Concepts

- **_safe_query()** (8 connections) — `src/hal0/metrics/read.py`
- **models_health()** (8 connections) — `src/hal0/metrics/read.py`
- **stats_summary()** (7 connections) — `src/hal0/metrics/read.py`
- **read.py** (6 connections) — `src/hal0/metrics/read.py`
- **system_stats()** (6 connections) — `src/hal0/metrics/read.py`
- **Any** (4 connections)
- **_percentile()** (3 connections) — `src/hal0/metrics/read.py`
- **Path** (3 connections)
- **Connection** (1 connections)
- **Row** (1 connections)
- **Read API (§21.3) -- thin queries over the OBS-1 tables.  Backs ``GET /api/stats`** (1 connections) — `src/hal0/metrics/read.py`
- **Run a SELECT, returning [] on any error (e.g. table not yet migrated).** (1 connections) — `src/hal0/metrics/read.py`
- **``GET /api/system-stats`` payload: latest fleet reading + per-slot latest sample** (1 connections) — `src/hal0/metrics/read.py`
- **``GET /api/stats`` payload: totals + per-(model,runner,device,modality) rollup.** (1 connections) — `src/hal0/metrics/read.py`
- **``GET /api/models/health`` payload -- one row per dispatchable slot.      ``slot** (1 connections) — `src/hal0/metrics/read.py`

## Relationships

- [connect](connect.md) (3 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/read.py`

## Audit Trail

- EXTRACTED: 48 (92%)
- INFERRED: 4 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*