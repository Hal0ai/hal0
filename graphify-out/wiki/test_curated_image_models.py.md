# test_curated_image_models.py

> 22 nodes

## Key Concepts

- **test_curated_image_models.py** (11 connections) — `tests/registry/test_curated_image_models.py`
- **test_run_pull_writes_to_comfyui_subdir()** (5 connections) — `tests/registry/test_curated_image_models.py`
- **test_final_path_routes_to_comfyui_subdir()** (4 connections) — `tests/registry/test_curated_image_models.py`
- **test_run_pull_default_subdir_unchanged()** (4 connections) — `tests/registry/test_curated_image_models.py`
- **MonkeyPatch** (3 connections)
- **test_final_path_falls_back_to_default_layout()** (3 connections) — `tests/registry/test_curated_image_models.py`
- **test_comfyui_subdir_is_path_safe()** (3 connections) — `tests/registry/test_curated_image_models.py`
- **test_curated_catalogue_includes_image_entries()** (2 connections) — `tests/registry/test_curated_image_models.py`
- **test_curated_image_entries_have_workflow_metadata()** (2 connections) — `tests/registry/test_curated_image_models.py`
- **test_curated_chat_entries_keep_chat_capability()** (2 connections) — `tests/registry/test_curated_image_models.py`
- **test_get_curated_lookup_for_image_entries()** (2 connections) — `tests/registry/test_curated_image_models.py`
- **test_comfyui_subdir_empty_falls_back_to_checkpoints()** (2 connections) — `tests/registry/test_curated_image_models.py`
- **Tests for the curated image-gen entries + ComfyUI-subdir pull routing.  Two surf** (1 connections) — `tests/registry/test_curated_image_models.py`
- **The named v1 image-gen picks must always be present.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **Every image-gen entry needs model_class + comfyui_subdir + capability.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **The chat picks must still default capability='chat' (no model_class).** (1 connections) — `tests/registry/test_curated_image_models.py`
- **An entry with comfyui_subdir lands under the ComfyUI models tree.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **Without comfyui_subdir, the legacy /var/lib/hal0/models layout wins.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **A malicious comfyui_subdir can't escape the comfyui/models tree.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **An empty/whitespace subdir lands in checkpoints/ as the safe default.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **End-to-end: a pull with comfyui_subdir lands under the right tree.** (1 connections) — `tests/registry/test_curated_image_models.py`
- **A pull without comfyui_subdir keeps the legacy models/<id>/ layout.** (1 connections) — `tests/registry/test_curated_image_models.py`

## Relationships

- [run_pull](run_pull.md) (4 shared connections)
- [Path](Path.md) (2 shared connections)
- [get_curated](get_curated.md) (1 shared connections)

## Source Files

- `tests/registry/test_curated_image_models.py`

## Audit Trail

- EXTRACTED: 46 (87%)
- INFERRED: 7 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*