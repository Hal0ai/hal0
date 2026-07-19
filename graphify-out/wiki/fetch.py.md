# fetch.py

> 16 nodes · cohesion 0.16

## Key Concepts

- **fetch.py** (10 connections) — `src/hal0/comfyui/fetch.py`
- **_provision_workflow()** (7 connections) — `src/hal0/comfyui/fetch.py`
- **_workflows_dir()** (5 connections) — `src/hal0/comfyui/fetch.py`
- **_run_sequence()** (4 connections) — `src/hal0/comfyui/fetch.py`
- **_scripts_dir()** (4 connections) — `src/hal0/comfyui/fetch.py`
- **_workflows_src_dir()** (4 connections) — `src/hal0/comfyui/fetch.py`
- **Path** (3 connections)
- **get_job()** (2 connections) — `src/hal0/comfyui/fetch.py`
- **test_provision_workflow_copies_matching_json()** (2 connections) — `tests/comfyui/test_fetch_ws_g.py`
- **Task 2.4: Async model fetch wrapper for ComfyUI scripts.  Public API:     fetch_** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Target dir to provision curated workflow JSONs into.      Honours ``COMFYUI_WORK** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Copy the variant's curated workflow JSON into the workflows dir.      Best-effor** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Background worker: run each fetch step sequentially.      Stops on first nonzero** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Return job dict (without internal fields) or None if unknown.  Live status.** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Return the directory holding the ComfyUI model-fetch scripts.      ``install.sh`** (1 connections) — `src/hal0/comfyui/fetch.py`
- **Return the directory holding curated ComfyUI workflow JSONs.      Ships alongsid** (1 connections) — `src/hal0/comfyui/fetch.py`

## Relationships

- [fetch_model](fetch_model.md) (5 shared connections)
- [test_fetch_ws_g.py](test_fetch_ws_g.py.md) (3 shared connections)
- [ModelVariant](ModelVariant.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)

## Source Files

- `src/hal0/comfyui/fetch.py`
- `tests/comfyui/test_fetch_ws_g.py`

## Audit Trail

- EXTRACTED: 44 (92%)
- INFERRED: 4 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*