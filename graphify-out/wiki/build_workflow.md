# build_workflow

> 21 nodes

## Key Concepts

- **build_workflow()** (21 connections) — `src/hal0/providers/comfyui_workflows.py`
- **comfyui_workflows.py** (12 connections) — `src/hal0/providers/comfyui_workflows.py`
- **WorkflowTemplateError** (7 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_load_template()** (6 connections) — `src/hal0/providers/comfyui_workflows.py`
- **template_for_model_class()** (5 connections) — `src/hal0/providers/comfyui_workflows.py`
- **WorkflowTemplateNotFound** (4 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Any** (4 connections)
- **test_build_workflow_empty_prompt_raises()** (4 connections) — `tests/providers/test_comfyui_workflows.py`
- **_parse_size()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_coerce_int()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_coerce_float()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **test_template_for_known_model_classes()** (2 connections) — `tests/providers/test_comfyui_workflows.py`
- **test_template_for_unknown_falls_back_to_sdxl_turbo()** (2 connections) — `tests/providers/test_comfyui_workflows.py`
- **OpenAI ``/v1/images/generations`` → ComfyUI prompt-graph translator.  ComfyUI ex** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Translator failed to build a usable ComfyUI workflow.** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **No template ships for the requested model_class.** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Read a workflow JSON from the package resources.      ``importlib.resources`` ke** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Resolve a model_class string to a template stem.      Falls back to ``sdxl_turbo** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Parse OpenAI-style ``"1024x1024"`` → ``(1024, 1024)``.      Tolerates capital ``** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Materialise a ComfyUI prompt graph for one image-gen request.      Args:** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **# NOTE: Flux model classes (e.g. flux-klein) want their own template** (1 connections) — `src/hal0/providers/comfyui_workflows.py`

## Relationships

- [test_comfyui_workflows.py](test_comfyui_workflows.py.md) (14 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)

## Source Files

- `src/hal0/providers/comfyui_workflows.py`
- `tests/providers/test_comfyui_workflows.py`

## Audit Trail

- EXTRACTED: 65 (77%)
- INFERRED: 19 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*