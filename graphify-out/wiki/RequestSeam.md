# RequestSeam

> 27 nodes

## Key Concepts

- **RequestSeam** (27 connections) — `src/hal0/metrics/seam.py`
- **_FakeWriter** (14 connections) — `tests/metrics/test_seam.py`
- **test_seam.py** (11 connections) — `tests/metrics/test_seam.py`
- **_FakeRequest** (10 connections) — `tests/metrics/test_seam.py`
- **.test_ttft_and_completion_row_written_after_stream_ends()** (7 connections) — `tests/metrics/test_seam.py`
- **seam()** (6 connections) — `tests/metrics/test_seam.py`
- **.test_writes_one_request_metric_row()** (6 connections) — `tests/metrics/test_seam.py`
- **_FakeCall** (5 connections) — `tests/metrics/test_seam.py`
- **.test_record_nonstreaming_is_noop_when_disabled()** (5 connections) — `tests/metrics/test_seam.py`
- **TestWrapStreaming** (5 connections) — `tests/metrics/test_seam.py`
- **.test_disabled_seam_returns_response_unwrapped()** (5 connections) — `tests/metrics/test_seam.py`
- **TestDisabledSeam** (4 connections) — `tests/metrics/test_seam.py`
- **.test_record_error_is_noop_when_disabled()** (4 connections) — `tests/metrics/test_seam.py`
- **TestRecordNonstreaming** (4 connections) — `tests/metrics/test_seam.py`
- **.test_call_none_writes_null_slot_and_model()** (4 connections) — `tests/metrics/test_seam.py`
- **TestRecordError** (4 connections) — `tests/metrics/test_seam.py`
- **.test_writes_ok_zero_with_error_code()** (4 connections) — `tests/metrics/test_seam.py`
- **.test_falls_back_to_exception_class_name()** (4 connections) — `tests/metrics/test_seam.py`
- **._drain()** (4 connections) — `tests/metrics/test_seam.py`
- **_FakeClient** (3 connections) — `tests/metrics/test_seam.py`
- **StreamingResponse** (3 connections)
- **.enqueue()** (2 connections) — `tests/metrics/test_seam.py`
- **writer()** (2 connections) — `tests/metrics/test_seam.py`
- **Captures one ``request_metric`` row per request through the v1 seam.** (1 connections) — `src/hal0/metrics/seam.py`
- **.__init__()** (1 connections) — `tests/metrics/test_seam.py`
- *... and 2 more nodes in this community*

## Relationships

- [.record_error](record_error.md) (4 shared connections)
- [MetricsWriter](MetricsWriter.md) (2 shared connections)
- [MetricsService](MetricsService.md) (2 shared connections)
- [profile.py](profile.py.md) (2 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [test_board_dispatch.py](test_board_dispatch.py.md) (1 shared connections)
- [test_journal_routes.py](test_journal_routes.py.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/seam.py`
- `tests/metrics/test_seam.py`

## Audit Trail

- EXTRACTED: 123 (84%)
- INFERRED: 24 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*