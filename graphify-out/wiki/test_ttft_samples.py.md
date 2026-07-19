# test_ttft_samples.py

> 17 nodes

## Key Concepts

- **test_ttft_samples.py** (20 connections) — `tests/slots/test_ttft_samples.py`
- **test_first_chunk_clamps_negative_delta_to_zero()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **test_stale_samples_are_evicted_from_reads_past_the_window()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **test_window_cutoff_boundary_is_inclusive()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **test_default_window_matches_module_constant()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_request_started_then_first_chunk_records_ttft()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_first_chunk_for_unknown_request_returns_none_and_records_nothing()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_request_cancelled_drops_inflight_without_recording_a_sample()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_request_cancelled_is_a_noop_for_unknown_id()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_current_ttft_and_avg_with_no_samples_is_none()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_current_ttft_returns_the_latest_sample()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_avg_ttft_is_windowed_mean_of_recent_samples()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **test_ttft_samples_deque_maxlen_bounds_memory()** (1 connections) — `tests/slots/test_ttft_samples.py`
- **Tests for the rolling TTFT sample window + fleet-wide aggregation.  ``hal0.slots** (1 connections) — `tests/slots/test_ttft_samples.py`
- **Defensive clamp: if `now` regresses relative to the recorded start     (clock sk** (1 connections) — `tests/slots/test_ttft_samples.py`
- **A sample older than `window_s` must be excluded from current/avg/count     reads** (1 connections) — `tests/slots/test_ttft_samples.py`
- **`_recent` keeps samples with ``ts >= now - window_s`` (a sample     landing exac** (1 connections) — `tests/slots/test_ttft_samples.py`

## Relationships

- [avg_ttft_across](avg_ttft_across.md) (3 shared connections)
- [ttft_samples.py](ttft_samples.py.md) (2 shared connections)
- [samples_from_events](samples_from_events.md) (2 shared connections)

## Source Files

- `tests/slots/test_ttft_samples.py`

## Audit Trail

- EXTRACTED: 39 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*