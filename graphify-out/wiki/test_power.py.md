# test_power.py

> 18 nodes · cohesion 0.18

## Key Concepts

- **test_power.py** (6 connections) — `tests/api/test_power.py`
- **.test_all_fields_correct()** (6 connections) — `tests/api/test_power.py`
- **.test_missing_power1_average_gpu_power_null()** (6 connections) — `tests/api/test_power.py`
- **_make_hwmon_tree()** (5 connections) — `tests/api/test_power.py`
- **.test_empty_tree_all_null_200()** (5 connections) — `tests/api/test_power.py`
- **Path** (4 connections)
- **TestClient** (4 connections)
- **power_client()** (3 connections) — `tests/api/test_power.py`
- **MonkeyPatch** (3 connections)
- **TestPowerHappyPath** (2 connections) — `tests/api/test_power.py`
- **TestPowerMissingTree** (2 connections) — `tests/api/test_power.py`
- **TestPowerPartialAmdgpu** (2 connections) — `tests/api/test_power.py`
- **Tests for GET /api/stats/power.  The router is NOT yet wired into the main app (** (1 connections) — `tests/api/test_power.py`
- **Non-existent hwmon root -> all four fields null, endpoint 200.** (1 connections) — `tests/api/test_power.py`
- **amdgpu dir present but power1_average absent -> gpu_power_w null.          temp1** (1 connections) — `tests/api/test_power.py`
- **Minimal FastAPI app with only the power router mounted.** (1 connections) — `tests/api/test_power.py`
- **Build a fake /sys/class/hwmon tree under tmp_path.      Layout:       hwmon0/  n** (1 connections) — `tests/api/test_power.py`
- **Full fake hwmon tree returns correct scaled values for all 4 fields.** (1 connections) — `tests/api/test_power.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_power.py`

## Audit Trail

- EXTRACTED: 54 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*