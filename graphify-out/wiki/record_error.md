# .record_error

> 16 nodes · cohesion 0.20

## Key Concepts

- **.record_error()** (7 connections) — `src/hal0/metrics/seam.py`
- **.record_nonstreaming()** (7 connections) — `src/hal0/metrics/seam.py`
- **_client_host()** (6 connections) — `src/hal0/metrics/seam.py`
- **_current_request_id()** (6 connections) — `src/hal0/metrics/seam.py`
- **.wrap_streaming()** (6 connections) — `src/hal0/metrics/seam.py`
- **truncate_client()** (5 connections) — `src/hal0/metrics/capture.py`
- **Request** (5 connections)
- **seam.py** (4 connections) — `src/hal0/metrics/seam.py`
- **TestTruncateClient** (4 connections) — `tests/metrics/test_capture.py`
- **.test_empty_string_stays_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_none_stays_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_truncates_to_24_chars()** (2 connections) — `tests/metrics/test_capture.py`
- **BaseException** (1 connections)
- **StreamingResponse** (1 connections)
- **RequestSeam -- the ONE T1 measurement point (plan §7.6 / S12).  Wraps ``api/rout** (1 connections) — `src/hal0/metrics/seam.py`
- **Best-effort read of the id ``request_id.install()`` bound this request to.** (1 connections) — `src/hal0/metrics/seam.py`

## Relationships

- [build_request_metric_row](build_request_metric_row.md) (5 shared connections)
- [RequestSeam](RequestSeam.md) (4 shared connections)
- [UpstreamCall](UpstreamCall.md) (3 shared connections)

## Source Files

- `src/hal0/metrics/capture.py`
- `src/hal0/metrics/seam.py`
- `tests/metrics/test_capture.py`

## Audit Trail

- EXTRACTED: 49 (82%)
- INFERRED: 11 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*