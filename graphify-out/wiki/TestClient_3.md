# TestClient

> 27 nodes · cohesion 0.17

## Key Concepts

- **TestClient** (26 connections)
- **MonkeyPatch** (10 connections)
- **Path** (8 connections)
- **TestStatusTelemetry** (8 connections) — `tests/api/test_comfyui_phase4.py`
- **TestWorkflowLaunch** (7 connections) — `tests/api/test_comfyui_phase4.py`
- **._patch_status()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_falls_back_to_user_default_dir()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_reads_workflow_and_posts_to_prompt()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **TestWorkflowList** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_it_s_eta_step_exist_as_null()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_util_is_none_or_zero_when_no_running_job()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_404_when_workflow_missing()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_read_error_does_not_leak_raw_exception()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_lists_json_files_from_primary_dir()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_missing_dir_returns_empty_list_not_error()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_primary_wins_over_user_fallback()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_traversal_names_are_filtered()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_running_status_surfaces_live_gpu_telemetry()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_status_memory_fields_present()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_rejects_invalid_workflow_name_before_filesystem_lookup()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_rejects_slash_workflow_name_without_filesystem_lookup()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_telemetry_probe_failure_is_fail_soft()** (2 connections) — `tests/api/test_comfyui_phase4.py`
- **Happy path: workflow file found, POSTed to /prompt, 202 returned.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **If primary dir has no match, fall back to user/default/workflows/.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **Ensure /status fields needed by the pane are present and well-formed.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- *... and 2 more nodes in this community*

## Relationships

- [test_comfyui_phase4.py](test_comfyui_phase4.py.md) (9 shared connections)
- [TestPreview](TestPreview.md) (3 shared connections)
- [TestRenderCancel](TestRenderCancel.md) (2 shared connections)

## Source Files

- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 130 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*