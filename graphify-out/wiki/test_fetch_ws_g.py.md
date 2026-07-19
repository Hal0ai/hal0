# test_fetch_ws_g.py

> 21 nodes

## Key Concepts

- **test_fetch_ws_g.py** (15 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **_fetch_env()** (6 connections) — `src/hal0/comfyui/fetch.py`
- **test_fetch_subprocess_receives_hf_token()** (6 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **_PopenRecorder** (5 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_fetch_model_provisions_workflow()** (5 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **_wait_done()** (3 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **_make_proc()** (3 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **_registry_workflow_names()** (3 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_no_script_hardcodes_container_hf_path()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_hf_scripts_resolve_from_path_with_container_fallback()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_fetch_env_promotes_hf_token()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_fetch_env_mirrors_legacy_token_name()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_fetch_env_forwards_hf_home()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_every_registry_workflow_json_is_shipped()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **test_shipped_workflows_are_api_format_json()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **Build the subprocess env for a fetch step, forwarding HF credentials.      Inher** (1 connections) — `src/hal0/comfyui/fetch.py`
- **.__init__()** (1 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **.__call__()** (1 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **#1110 (WS-G): ComfyUI fetch fixes.  Covers the three ways the fetch was feature-** (1 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **No get_*.sh may pin the download tool to the container venv path alone.** (1 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **Each hf-using script resolves `hf` from PATH, keeping the venv fallback.** (1 connections) — `tests/comfyui/test_fetch_ws_g.py`

## Relationships

- [fetch.py](fetch.py.md) (3 shared connections)
- [fetch_model](fetch_model.md) (2 shared connections)
- [ModelVariant](ModelVariant.md) (1 shared connections)

## Source Files

- `src/hal0/comfyui/fetch.py`
- `tests/comfyui/test_fetch_ws_g.py`

## Audit Trail

- EXTRACTED: 57 (86%)
- INFERRED: 9 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*