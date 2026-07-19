# parse_json_object

> 7 nodes

## Key Concepts

- **parse_json_object()** (8 connections) — `src/hal0/metrics/capture.py`
- **TestParseJsonObject** (5 connections) — `tests/metrics/test_capture.py`
- **.test_valid_object()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_invalid_json_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_non_object_json_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_empty_bytes_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- **Best-effort JSON object parse. Returns None on any failure/non-dict.** (1 connections) — `src/hal0/metrics/capture.py`

## Relationships

- [build_request_metric_row](build_request_metric_row.md) (3 shared connections)
- [.record_error](record_error.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/capture.py`
- `tests/metrics/test_capture.py`

## Audit Trail

- EXTRACTED: 13 (59%)
- INFERRED: 9 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*