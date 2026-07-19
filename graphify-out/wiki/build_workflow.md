# build_workflow

> 27 nodes · cohesion 0.14

## Key Concepts

- **build_workflow()** (21 connections) — `src/hal0/providers/comfyui_workflows.py`
- **test_comfyui_workflows.py** (16 connections) — `tests/providers/test_comfyui_workflows.py`
- **_baseline_body()** (13 connections) — `tests/providers/test_comfyui_workflows.py`
- **template_for_model_class()** (5 connections) — `src/hal0/providers/comfyui_workflows.py`
- **test_build_workflow_clamps_size_to_safe_range()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_empty_prompt_raises()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_patches_ckpt_filename()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_random_seed_when_unspecified()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_strips_meta_block()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_negative_prompt_patches_node7()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_sd15_template_for_sd15_class()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_seed_override()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_steps_and_cfg_overrides()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_substitutes_prompt()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_build_workflow_substitutes_size_and_batch()** (3 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_model_class_table_has_named_entries()** (2 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_template_for_known_model_classes()** (2 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_template_for_unknown_falls_back_to_sdxl_turbo()** (2 connections) — `tests/providers/test_comfyui_workflows.py`
- **Resolve a model_class string to a template stem.      Falls back to ``sdxl_turbo** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Materialise a ComfyUI prompt graph for one image-gen request.      Args:** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Any** (1 connections)
- **Unit tests for the OpenAI → ComfyUI workflow translator.  The translator is a pu** (1 connections) — `tests/providers/test_comfyui_workflows.py`
- **Two calls without an explicit seed produce different seeds (probabilistically).** (1 connections) — `tests/providers/test_comfyui_workflows.py`
- **The CheckpointLoaderSimple node holds the actual model filename.** (1 connections) — `tests/providers/test_comfyui_workflows.py`
- **The _meta block is template-only; ComfyUI's /prompt would 422 on it.** (1 connections) — `tests/providers/test_comfyui_workflows.py`
- *... and 2 more nodes in this community*

## Relationships

- [comfyui_workflows.py](comfyui_workflows.py.md) (9 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)

## Source Files

- `src/hal0/providers/comfyui_workflows.py`
- `tests/providers/test_comfyui_workflows.py`

## Audit Trail

- EXTRACTED: 80 (74%)
- INFERRED: 28 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*