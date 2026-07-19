# _list_workflow_names

> 10 nodes · cohesion 0.22

## Key Concepts

- **_list_workflow_names()** (6 connections) — `src/hal0/api/routes/comfyui.py`
- **_find_workflow()** (5 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_data_dir()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **comfyui_workflows()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **_comfyui_workflows_dir()** (4 connections) — `src/hal0/api/routes/comfyui.py`
- **Primary workflow directory — env override for tests; default is the bind-mount p** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Root of the ComfyUI data directory (for fallback user/default/workflows path).** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Locate <name>.json, trying primary then user/default fallback. None if absent.** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **Enumerate launchable ``<name>.json`` workflows across both search dirs.      Sca** (1 connections) — `src/hal0/api/routes/comfyui.py`
- **List launchable workflow templates discoverable on disk.      Returns ``{"workfl** (1 connections) — `src/hal0/api/routes/comfyui.py`

## Relationships

- [comfyui.py](comfyui.py.md) (8 shared connections)

## Source Files

- `src/hal0/api/routes/comfyui.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*