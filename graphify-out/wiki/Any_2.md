# Any

> 14 nodes

## Key Concepts

- **Any** (14 connections)
- **_gpu_telemetry()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **_parse_memory()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_latest_output_image()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_arbiter_api_mode()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **_run_switch()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **_bytes_to_gb()** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **_as_pct()** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **_mhz_to_ghz()** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **Fold ComfyUI's /system_stats into the pane's GTT + RAM gauges.      GTT comes fr** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Read live GPU util/temp/clock for /status, degrading per-field to null.      ``g** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Arbiter-truth current mode ("generation"|"inference"), or None.      None means** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Drive the arbiter for ``mode``; record failure for /status to surface.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Find the newest output image entry in ComfyUI's /history response.      ComfyUI** (1 connections) — `src/hal0/api/routes/comfyui.py`

## Relationships

- [comfyui.py](comfyui.py.md) (11 shared connections)
- [comfyui_switchover](comfyui_switchover.md) (4 shared connections)
- [_fetch_json](_fetch_json.md) (3 shared connections)
- [_list_workflow_names](_list_workflow_names.md) (2 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (1 shared connections)
- [_probe_power](_probe_power.md) (1 shared connections)
- [_memory_subgraph.py](_memory_subgraph.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`

## Audit Trail

- EXTRACTED: 50 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*