# MetricsRetention

> 14 nodes · cohesion 0.18

## Key Concepts

- **MetricsRetention** (9 connections) — `src/hal0/metrics/retention.py`
- **prune()** (5 connections) — `src/hal0/metrics/retention.py`
- **retention.py** (4 connections) — `src/hal0/metrics/retention.py`
- **._loop()** (3 connections) — `src/hal0/metrics/retention.py`
- **_cutoff_iso()** (2 connections) — `src/hal0/metrics/retention.py`
- **.__init__()** (2 connections) — `src/hal0/metrics/retention.py`
- **.run_once()** (2 connections) — `src/hal0/metrics/retention.py`
- **.start()** (2 connections) — `src/hal0/metrics/retention.py`
- **.stop()** (1 connections) — `src/hal0/metrics/retention.py`
- **Connection** (1 connections)
- **Path** (1 connections)
- **Bounded storage -- background auto-prune (plan §13.5: "never fill a user's disk"** (1 connections) — `src/hal0/metrics/retention.py`
- **Delete rows older than each table's retention window. Returns counts deleted.** (1 connections) — `src/hal0/metrics/retention.py`
- **Background task: prune on an interval (default every 6h).** (1 connections) — `src/hal0/metrics/retention.py`

## Relationships

- [MetricsService](MetricsService.md) (2 shared connections)
- [tx](tx.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/retention.py`

## Audit Trail

- EXTRACTED: 32 (91%)
- INFERRED: 3 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*