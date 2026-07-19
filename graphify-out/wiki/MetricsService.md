# MetricsService

> 29 nodes · cohesion 0.08

## Key Concepts

- **MetricsService** (10 connections) — `src/hal0/metrics/service.py`
- **.__init__()** (10 connections) — `src/hal0/metrics/service.py`
- **MetricsAggregator** (9 connections) — `src/hal0/metrics/aggregator.py`
- **config.py** (7 connections) — `src/hal0/metrics/config.py`
- **load_metrics_settings()** (5 connections) — `src/hal0/metrics/config.py`
- **MetricsSettings** (4 connections) — `src/hal0/metrics/config.py`
- **._loop()** (3 connections) — `src/hal0/metrics/aggregator.py`
- **.run_once()** (3 connections) — `src/hal0/metrics/aggregator.py`
- **_read_toml_metrics_table()** (3 connections) — `src/hal0/metrics/config.py`
- **.__init__()** (2 connections) — `src/hal0/metrics/aggregator.py`
- **.start()** (2 connections) — `src/hal0/metrics/aggregator.py`
- **service.py** (2 connections) — `src/hal0/metrics/service.py`
- **.start()** (2 connections) — `src/hal0/metrics/service.py`
- **.stop()** (1 connections) — `src/hal0/metrics/aggregator.py`
- **Path** (1 connections)
- **Background task: aggregate the most recently completed hour, on interval.** (1 connections) — `src/hal0/metrics/aggregator.py`
- **Aggregate the last fully-elapsed hour. Returns rows written.** (1 connections) — `src/hal0/metrics/aggregator.py`
- **_env_bool()** (1 connections) — `src/hal0/metrics/config.py`
- **_env_float()** (1 connections) — `src/hal0/metrics/config.py`
- **_env_int()** (1 connections) — `src/hal0/metrics/config.py`
- **Metrics configuration -- a standalone reader, not part of ``Hal0Config``.  ``con** (1 connections) — `src/hal0/metrics/config.py`
- **Operator-tunable knobs for the OBS-1 metrics core.      Every field has a shippe** (1 connections) — `src/hal0/metrics/config.py`
- **Best-effort read of ``[metrics]`` from hal0.toml. Never raises.** (1 connections) — `src/hal0/metrics/config.py`
- **Resolve :class:`MetricsSettings` from TOML (best-effort) + env overrides.      P** (1 connections) — `src/hal0/metrics/config.py`
- **.stop()** (1 connections) — `src/hal0/metrics/service.py`
- *... and 4 more nodes in this community*

## Relationships

- [MetricsRetention](MetricsRetention.md) (2 shared connections)
- [SlotSampler](SlotSampler.md) (2 shared connections)
- [RequestSeam](RequestSeam.md) (2 shared connections)
- [MetricsWriter](MetricsWriter.md) (2 shared connections)
- [aggregate_hour](aggregate_hour.md) (1 shared connections)
- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/aggregator.py`
- `src/hal0/metrics/config.py`
- `src/hal0/metrics/service.py`

## Audit Trail

- EXTRACTED: 63 (81%)
- INFERRED: 15 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*