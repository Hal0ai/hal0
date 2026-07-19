# MetricsWriter

> 31 nodes · cohesion 0.09

## Key Concepts

- **MetricsWriter** (23 connections) — `src/hal0/metrics/writer.py`
- **TestMetricsWriter** (7 connections) — `tests/metrics/test_writer.py`
- **Path** (6 connections)
- **.ensure_schema()** (4 connections) — `src/hal0/metrics/writer.py`
- **.test_batches_multiple_rows_in_one_transaction()** (4 connections) — `tests/metrics/test_writer.py`
- **.test_enqueued_row_is_written()** (4 connections) — `tests/metrics/test_writer.py`
- **.test_ensure_schema_creates_tables()** (4 connections) — `tests/metrics/test_writer.py`
- **.test_overflow_drops_oldest_not_raises()** (4 connections) — `tests/metrics/test_writer.py`
- **writer.py** (3 connections) — `src/hal0/metrics/writer.py`
- **_insert_sql()** (3 connections) — `src/hal0/metrics/writer.py`
- **.db_path()** (3 connections) — `src/hal0/metrics/writer.py`
- **._drain_loop()** (3 connections) — `src/hal0/metrics/writer.py`
- **.enqueue()** (3 connections) — `src/hal0/metrics/writer.py`
- **.start()** (3 connections) — `src/hal0/metrics/writer.py`
- **test_writer.py** (3 connections) — `tests/metrics/test_writer.py`
- **.test_stop_is_safe_when_never_started()** (3 connections) — `tests/metrics/test_writer.py`
- **.__init__()** (2 connections) — `src/hal0/metrics/seam.py`
- **.__init__()** (2 connections) — `src/hal0/metrics/writer.py`
- **.stats()** (2 connections) — `src/hal0/metrics/writer.py`
- **Any** (2 connections)
- **Path** (2 connections)
- **db_path()** (2 connections) — `tests/metrics/test_writer.py`
- **.stop()** (1 connections) — `src/hal0/metrics/writer.py`
- **Async, batched, off-hot-path SQLite writer for the metrics tables.  One bounded** (1 connections) — `src/hal0/metrics/writer.py`
- **Diagnostic counters -- surfaced by ``hal0 metrics status``.** (1 connections) — `src/hal0/metrics/writer.py`
- *... and 6 more nodes in this community*

## Relationships

- [connect](connect.md) (4 shared connections)
- [tx](tx.md) (3 shared connections)
- [RequestSeam](RequestSeam.md) (2 shared connections)
- [MetricsService](MetricsService.md) (2 shared connections)
- [SlotSampler](SlotSampler.md) (1 shared connections)
- [_probe_power](_probe_power.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/seam.py`
- `src/hal0/metrics/writer.py`
- `tests/metrics/test_writer.py`

## Audit Trail

- EXTRACTED: 81 (80%)
- INFERRED: 20 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*