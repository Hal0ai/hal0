# TestClient

> 23 nodes

## Key Concepts

- **TestClient** (26 connections)
- **MonkeyPatch** (10 connections)
- **Path** (8 connections)
- **TestWorkflowLaunch** (7 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_reads_workflow_and_posts_to_prompt()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_falls_back_to_user_default_dir()** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **TestWorkflowList** (5 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_404_when_workflow_missing()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_read_error_does_not_leak_raw_exception()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_lists_json_files_from_primary_dir()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_primary_wins_over_user_fallback()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_missing_dir_returns_empty_list_not_error()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_traversal_names_are_filtered()** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **TestPreview** (4 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_rejects_invalid_workflow_name_before_filesystem_lookup()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_launch_rejects_slash_workflow_name_without_filesystem_lookup()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_preview_streams_image_bytes()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_preview_404_when_history_has_no_outputs()** (3 connections) — `tests/api/test_comfyui_phase4.py`
- **.test_preview_404_when_no_history()** (2 connections) — `tests/api/test_comfyui_phase4.py`
- **Happy path: workflow file found, POSTed to /prompt, 202 returned.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **If primary dir has no match, fall back to user/default/workflows/.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **When history has output, the image bytes are proxied back.** (1 connections) — `tests/api/test_comfyui_phase4.py`
- **History entry with empty outputs → 404.** (1 connections) — `tests/api/test_comfyui_phase4.py`

## Relationships

- [test_comfyui_phase4.py](test_comfyui_phase4.py.md) (9 shared connections)
- [TestStatusTelemetry](TestStatusTelemetry.md) (5 shared connections)
- [TestRenderCancel](TestRenderCancel.md) (2 shared connections)

## Source Files

- `tests/api/test_comfyui_phase4.py`

## Audit Trail

- EXTRACTED: 112 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*