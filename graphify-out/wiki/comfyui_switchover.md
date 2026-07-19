# comfyui_switchover

> 19 nodes

## Key Concepts

- **comfyui_switchover()** (16 connections) — `src/hal0/api/routes/comfyui.py`
- **JSONResponse** (11 connections)
- **_get_arbiter()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_pin()** (8 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_workflow_launch()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **UnprocessableEntity** (7 connections) — `src/hal0/errors.py`
- **Request** (6 connections)
- **comfyui_restart()** (6 connections) — `src/hal0/api/routes/comfyui.py`
- **_arbiter_unavailable()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_workflow_not_found_response()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_invalid_workflow_launch()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **BackgroundTasks** (2 connections)
- **The SlotManager's GpuArbiter off app.state, or None when unwired.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Flip the iGPU between LLM inference and ComfyUI generation.      Body: ``{"mode"** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Toggle the arbiter's manual pin (holds image mode against idle-restore).      Bo** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Restart the slot-managed ComfyUI runtime.      Runs in the background and return** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Quick-launch a workflow by name from the bind-mounted workflows directory.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Reject malformed workflow names that include path separators.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **422 — the request was well-formed but failed business-rule validation.      Dist** (1 connections) — `src/hal0/errors.py`

## Relationships

- [comfyui.py](comfyui.py.md) (17 shared connections)
- [_fetch_json](_fetch_json.md) (5 shared connections)
- [Any](Any.md) (4 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [_list_workflow_names](_list_workflow_names.md) (1 shared connections)
- [memory_admin.py](memory_admin.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`
- `src/hal0/errors.py`

## Audit Trail

- EXTRACTED: 82 (90%)
- INFERRED: 9 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*