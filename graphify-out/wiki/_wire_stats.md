# _wire_stats

> 22 nodes · cohesion 0.16

## Key Concepts

- **_wire_stats()** (9 connections) — `tests/api/test_cpu_util.py`
- **test_cpu_util.py** (6 connections) — `tests/api/test_cpu_util.py`
- **TestClient** (6 connections)
- **_MinimalStatsStub** (5 connections) — `tests/api/test_cpu_util.py`
- **MonkeyPatch** (5 connections)
- **.test_cpu_util_clamped_above_one()** (5 connections) — `tests/api/test_cpu_util.py`
- **.test_cpu_util_scaled_correctly()** (5 connections) — `tests/api/test_cpu_util.py`
- **.test_cpu_util_zero_is_valid()** (5 connections) — `tests/api/test_cpu_util.py`
- **.test_psutil_none_gives_cpu_util_none_endpoint_200()** (5 connections) — `tests/api/test_cpu_util.py`
- **.test_psutil_raises_gives_cpu_util_none_endpoint_200()** (5 connections) — `tests/api/test_cpu_util.py`
- **TestCpuUtilHappyPath** (4 connections) — `tests/api/test_cpu_util.py`
- **TestCpuUtilUnavailable** (3 connections) — `tests/api/test_cpu_util.py`
- **.gpu_sample()** (1 connections) — `tests/api/test_cpu_util.py`
- **.snapshot()** (1 connections) — `tests/api/test_cpu_util.py`
- **Tests for cpu_util field added to /api/stats/hardware (non-blocking psutil poll)** (1 connections) — `tests/api/test_cpu_util.py`
- **If psutil.cpu_percent() raises, cpu_util is absent/None — no 500.** (1 connections) — `tests/api/test_cpu_util.py`
- **HardwareStats stand-in returning a minimal snapshot (no GPU fields).** (1 connections) — `tests/api/test_cpu_util.py`
- **Attach the minimal stats stub to the app so _local_live_stats runs.** (1 connections) — `tests/api/test_cpu_util.py`
- **psutil.cpu_percent(42.0) -> cpu_util == 0.42 in response.** (1 connections) — `tests/api/test_cpu_util.py`
- **A reading over 100 pct (e.g. transient kernel artifact) clamps to 1.0.** (1 connections) — `tests/api/test_cpu_util.py`
- **0.0 pct (e.g. first-call prime on some kernels) -> 0.0, not None.** (1 connections) — `tests/api/test_cpu_util.py`
- **When psutil could not be imported (_psutil=None), cpu_util is None         and t** (1 connections) — `tests/api/test_cpu_util.py`

## Relationships

- [types.py](types.py.md) (1 shared connections)

## Source Files

- `tests/api/test_cpu_util.py`

## Audit Trail

- EXTRACTED: 73 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*