# test_fetch_paths.py

> 18 nodes

## Key Concepts

- **test_fetch_paths.py** (8 connections) — `tests/comfyui/test_fetch_paths.py`
- **Path** (7 connections)
- **MonkeyPatch** (5 connections)
- **test_scripts_dir_falls_back_to_fhs_for_wheel_install()** (4 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_scripts_dir_prefers_editable_when_both_exist()** (4 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_scripts_dir_missing_everywhere_returns_fhs_candidate()** (4 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_workflows_src_dir_falls_back_to_fhs_for_wheel_install()** (4 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_provision_workflow_finds_asset_via_fhs_fallback()** (4 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_scripts_dir_resolves_editable_when_present()** (3 connections) — `tests/comfyui/test_fetch_paths.py`
- **test_workflows_src_dir_resolves_editable_when_present()** (3 connections) — `tests/comfyui/test_fetch_paths.py`
- **Finding 5: _scripts_dir() / _workflows_src_dir() FHS-aware resolution.  Same cla** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **Editable / dev checkout: this test runs against the real checkout,     which has** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **A non-editable wheel install has ``fetch.py`` under     ``<venv>/lib/pythonX/sit** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **When an editable-shaped repo root has the scripts dir, it wins over     the FHS** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **When the scripts dir exists in neither location, the function     still returns** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **Editable / dev checkout: real checkout has     ``installer/comfyui/workflows`` t** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **Same FHS-fallback contract as _scripts_dir(), for the curated     workflow JSON** (1 connections) — `tests/comfyui/test_fetch_paths.py`
- **End-to-end: with fetch.py "installed" as a non-editable wheel,     _provision_wo** (1 connections) — `tests/comfyui/test_fetch_paths.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/comfyui/test_fetch_paths.py`

## Audit Trail

- EXTRACTED: 54 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*