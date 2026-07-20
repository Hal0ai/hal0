# _iso

> 10 nodes

## Key Concepts

- **_iso()** (6 connections) — `tests/metrics/test_retention.py`
- **TestPrune** (6 connections) — `tests/metrics/test_retention.py`
- **Path** (5 connections)
- **.test_prunes_old_request_metric_rows()** (4 connections) — `tests/metrics/test_retention.py`
- **.test_prunes_old_slot_sample_rows_on_a_shorter_window()** (4 connections) — `tests/metrics/test_retention.py`
- **.test_metric_rollup_survives_past_raw_retention()** (4 connections) — `tests/metrics/test_retention.py`
- **.test_rollup_pruned_past_its_own_retention()** (4 connections) — `tests/metrics/test_retention.py`
- **.test_prune_is_idempotent()** (4 connections) — `tests/metrics/test_retention.py`
- **test_retention.py** (3 connections) — `tests/metrics/test_retention.py`
- **prune() -- bounded storage: raw tables age out, rollup survives longer.** (1 connections) — `tests/metrics/test_retention.py`

## Relationships

- [connect](connect.md) (5 shared connections)

## Source Files

- `tests/metrics/test_retention.py`

## Audit Trail

- EXTRACTED: 36 (88%)
- INFERRED: 5 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*