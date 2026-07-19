# build_request_metric_row

> 33 nodes · cohesion 0.10

## Key Concepts

- **build_request_metric_row()** (11 connections) — `src/hal0/metrics/capture.py`
- **extract_timings_fields()** (8 connections) — `src/hal0/metrics/capture.py`
- **parse_json_object()** (8 connections) — `src/hal0/metrics/capture.py`
- **extract_usage_fields()** (7 connections) — `src/hal0/metrics/capture.py`
- **capture.py** (6 connections) — `src/hal0/metrics/capture.py`
- **test_capture.py** (6 connections) — `tests/metrics/test_capture.py`
- **TestBuildRequestMetricRow** (5 connections) — `tests/metrics/test_capture.py`
- **TestExtractTimingsFields** (5 connections) — `tests/metrics/test_capture.py`
- **TestParseJsonObject** (5 connections) — `tests/metrics/test_capture.py`
- **Any** (4 connections)
- **TestExtractUsageFields** (4 connections) — `tests/metrics/test_capture.py`
- **.test_comfyui_style_no_token_metric_stays_none()** (3 connections) — `tests/metrics/test_capture.py`
- **.test_error_row_has_ok_zero_and_error_code()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_falls_back_to_wall_clock_approx_when_no_exact_source()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_prefers_exact_timings_over_approx()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_cache_n_zero_is_cache_miss()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_exact_llama_timings()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_missing_timings_returns_empty()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_no_draft_fields_omits_spec_accept_rate()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_flm_decoding_speed_is_exact()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_missing_usage_returns_empty()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_openai_usage_and_finish_reason()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_empty_bytes_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_invalid_json_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- **.test_non_object_json_returns_none()** (2 connections) — `tests/metrics/test_capture.py`
- *... and 8 more nodes in this community*

## Relationships

- [.record_error](record_error.md) (5 shared connections)

## Source Files

- `src/hal0/metrics/capture.py`
- `tests/metrics/test_capture.py`

## Audit Trail

- EXTRACTED: 74 (69%)
- INFERRED: 33 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*