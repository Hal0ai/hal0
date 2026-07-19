# fixture

> 32 nodes · cohesion 0.12

## Key Concepts

- **fixture** (8 connections)
- **MonkeyPatch** (8 connections)
- **TestNpuResidencyUtil** (8 connections) — `tests/api/test_npu_util.py`
- **.test_npu_util_absent_when_no_prev_sample()** (7 connections) — `tests/api/test_npu_util.py`
- **.test_npu_util_present_on_valid_delta()** (7 connections) — `tests/api/test_npu_util.py`
- **._write_counters()** (7 connections) — `tests/api/test_npu_util.py`
- **_wire_stats()** (7 connections) — `tests/api/test_npu_util.py`
- **.test_npu_util_absent_when_files_missing()** (6 connections) — `tests/api/test_npu_util.py`
- **test_npu_util.py** (5 connections) — `tests/api/test_npu_util.py`
- **_MinimalStatsStub** (5 connections) — `tests/api/test_npu_util.py`
- **.test_counter_reset_returns_none_and_resets_cache()** (5 connections) — `tests/api/test_npu_util.py`
- **.test_first_call_returns_none()** (5 connections) — `tests/api/test_npu_util.py`
- **.test_second_call_returns_fraction()** (5 connections) — `tests/api/test_npu_util.py`
- **.test_zero_denom_returns_none()** (5 connections) — `tests/api/test_npu_util.py`
- **TestNpuUtilEndpoint** (5 connections) — `tests/api/test_npu_util.py`
- **TestClient** (4 connections)
- **.test_missing_files_returns_none()** (4 connections) — `tests/api/test_npu_util.py`
- **.gpu_sample()** (1 connections) — `tests/api/test_npu_util.py`
- **.snapshot()** (1 connections) — `tests/api/test_npu_util.py`
- **Tests for npu_util field added to /api/stats/hardware.  Covers: - Case1: first c** (1 connections) — `tests/api/test_npu_util.py`
- **When active+suspended don't change between reads, denom=0 -> None.** (1 connections) — `tests/api/test_npu_util.py`
- **When two reads have elapsed, npu_util appears in the response.** (1 connections) — `tests/api/test_npu_util.py`
- **First call returns None -> npu_util key absent from response.** (1 connections) — `tests/api/test_npu_util.py`
- **Missing sysfs files -> helper returns None -> key absent, 200 OK.** (1 connections) — `tests/api/test_npu_util.py`
- **HardwareStats stand-in returning a minimal snapshot (no GPU fields).** (1 connections) — `tests/api/test_npu_util.py`
- *... and 7 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_npu_util.py`

## Audit Trail

- EXTRACTED: 116 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*