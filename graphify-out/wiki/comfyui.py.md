# comfyui.py

> 72 nodes · cohesion 0.06

## Key Concepts

- **comfyui.py** (45 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_switchover()** (16 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_status()** (14 connections) — `src/hal0/api/routes/comfyui.py`
- **Any** (14 connections)
- **JSONResponse** (11 connections)
- **_fetch_json()** (9 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_pin()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_preview()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **_get_arbiter()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_base_url()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_models_fetch()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_workflow_launch()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **_get_client()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **_gpu_telemetry()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **UnprocessableEntity** (7 connections) — `src/hal0/errors.py`
- **comfyui_render_cancel()** (6 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_restart()** (6 connections) — `src/hal0/api/routes/comfyui.py`
- **Request** (6 connections)
- **_arbiter_unavailable()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_container_state()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_latest_output_image()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_model_inventory()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_parse_memory()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_systemd_active()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_arbiter_api_mode()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- *... and 47 more nodes in this community*

## Relationships

- [_list_workflow_names](_list_workflow_names.md) (8 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (7 shared connections)
- [ModelVariant](ModelVariant.md) (2 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [NpuTrioRouter](NpuTrioRouter.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [_probe_power](_probe_power.md) (1 shared connections)
- [_memory_subgraph.py](_memory_subgraph.py.md) (1 shared connections)
- [memory_admin.py](memory_admin.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`
- `src/hal0/errors.py`

## Audit Trail

- EXTRACTED: 294 (94%)
- INFERRED: 19 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*