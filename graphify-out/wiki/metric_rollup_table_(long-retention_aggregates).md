# metric_rollup table (long-retention aggregates)

> 6 nodes · cohesion 0.33

## Key Concepts

- **metric_rollup table (long-retention aggregates)** (2 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`
- **request_metric table (T1 per-request)** (2 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`
- **slot_sample table (T2 per-slot timeseries)** (2 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`
- **T2 per-slot background sampler task** (2 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`
- **RequestSeam (section 7.6 measurement seam, S12)** (1 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`
- **slot_event table (T2 slot lifecycle log)** (1 connections) — `docs/rework/hal0-specs/spec-obs-metrics.md`

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-obs-metrics.md`

## Audit Trail

- EXTRACTED: 10 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*