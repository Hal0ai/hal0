# comfyui.py

> 26 nodes

## Key Concepts

- **comfyui.py** (45 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_status()** (14 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_models_fetch()** (7 connections) — `src/hal0/api/routes/comfyui.py`
- **_container_state()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_systemd_active()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_model_inventory()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_container()** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **SwitchoverBody** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **SetPinnedBody** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **_FetchBody** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_logs()** (3 connections) — `src/hal0/api/routes/comfyui.py`
- **aclose_client()** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **_reset_state()** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_models_dir()** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **_count_models()** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **_engine_state()** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **_SelectionItem** (2 connections) — `src/hal0/api/routes/comfyui.py`
- **ComfyUI "generation engine" status aggregator + control routes.  The dashboard m** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Close the shared client on app shutdown. Idempotent.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Drop the shared client + switch tracker. For test isolation only.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Return the generation container state: 'running', 'exited', or 'absent'.      Po** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **True iff ``systemctl is-active <unit>`` reports the unit active.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Count weight files per category on the model share — VERIFIED, never faked.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Aggregate docker + systemd + ComfyUI HTTP into one engine-status object.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Trigger deferred model pulls for ComfyUI capabilities.      Body (one of):** (1 connections) — `src/hal0/api/routes/comfyui.py`
- *... and 1 more nodes in this community*

## Relationships

- [comfyui_switchover](comfyui_switchover.md) (17 shared connections)
- [Any](Any.md) (11 shared connections)
- [_fetch_json](_fetch_json.md) (7 shared connections)
- [_list_workflow_names](_list_workflow_names.md) (5 shared connections)
- [BaseModel](BaseModel.md) (4 shared connections)
- [_probe_comfyui](_probe_comfyui.md) (3 shared connections)
- [ModelVariant](ModelVariant.md) (2 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`

## Audit Trail

- EXTRACTED: 113 (97%)
- INFERRED: 4 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*