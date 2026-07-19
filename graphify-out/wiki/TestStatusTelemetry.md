# TestStatusTelemetry

> 10 nodes

## Key Concepts

- **TestStatusTelemetry** (8 connections) — `tests/api/test_comfyui_phase4.py`
- **._patch_status()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_util_is_none_or_zero_when_no_running_job()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_it_s_eta_step_exist_as_null()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_status_memory_fields_present()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_running_status_surfaces_live_gpu_telemetry()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_telemetry_probe_failure_is_fail_soft()** (2 connections) — `tests/api/test_comfyui_phase4.py`
- **Ensure /status fields needed by the pane are present and well-formed.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **gpu_busy_percent is forced-high artifact — util must be 0/None when idle.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **it/s, eta, step require a future ComfyUI websocket subscription.** (1 connections) — `tests/api/test_comfyui_phase4.py`

## Relationships

- [TestClient](TestClient.md) (5 shared connections)
- [test_comfyui_phase4.py](test_comfyui_phase4.py.md) (1 shared connections)

## Source Files

- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 32 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*