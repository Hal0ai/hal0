# aggregate_hour

> 18 nodes · cohesion 0.20

## Key Concepts

- **aggregate_hour()** (12 connections) — `src/hal0/metrics/aggregator.py`
- **aggregator.py** (7 connections) — `src/hal0/metrics/aggregator.py`
- **TestAggregateHour** (5 connections) — `tests/metrics/test_aggregator.py`
- **.test_groups_by_model_runner_device_modality()** (5 connections) — `tests/metrics/test_aggregator.py`
- **.test_idempotent_rerun_same_hour()** (5 connections) — `tests/metrics/test_aggregator.py`
- **.test_out_of_window_rows_excluded()** (5 connections) — `tests/metrics/test_aggregator.py`
- **Path** (4 connections)
- **_seed_request_metric()** (4 connections) — `tests/metrics/test_aggregator.py`
- **.test_slot_sample_hourly_rollup()** (4 connections) — `tests/metrics/test_aggregator.py`
- **_bucket_bounds()** (3 connections) — `src/hal0/metrics/aggregator.py`
- **datetime** (3 connections)
- **test_aggregator.py** (3 connections) — `tests/metrics/test_aggregator.py`
- **_avg()** (2 connections) — `src/hal0/metrics/aggregator.py`
- **_percentile()** (2 connections) — `src/hal0/metrics/aggregator.py`
- **Connection** (1 connections)
- **Background hourly/daily rollup -- downsamples T1/T2 raw rows into ``metric_rollu** (1 connections) — `src/hal0/metrics/aggregator.py`
- **Aggregate one hour of ``request_metric`` + ``slot_sample`` rows.      ``bucket_s** (1 connections) — `src/hal0/metrics/aggregator.py`
- **aggregate_hour() -- idempotent hourly rollup of request_metric + slot_sample.** (1 connections) — `tests/metrics/test_aggregator.py`

## Relationships

- [connect](connect.md) (4 shared connections)
- [MetricsService](MetricsService.md) (1 shared connections)
- [tx](tx.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/aggregator.py`
- `tests/metrics/test_aggregator.py`

## Audit Trail

- EXTRACTED: 55 (81%)
- INFERRED: 13 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*