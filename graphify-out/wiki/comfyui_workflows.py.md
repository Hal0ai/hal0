# comfyui_workflows.py

> 15 nodes · cohesion 0.18

## Key Concepts

- **comfyui_workflows.py** (12 connections) — `src/hal0/providers/comfyui_workflows.py`
- **WorkflowTemplateError** (7 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_load_template()** (6 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Any** (4 connections)
- **WorkflowTemplateNotFound** (4 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_coerce_float()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_coerce_int()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **_parse_size()** (3 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Hal0Error** (1 connections)
- **OpenAI ``/v1/images/generations`` → ComfyUI prompt-graph translator.  ComfyUI ex** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Parse OpenAI-style ``"1024x1024"`` → ``(1024, 1024)``.      Tolerates capital ``** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **# NOTE: Flux model classes (e.g. flux-klein) want their own template** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Translator failed to build a usable ComfyUI workflow.** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **No template ships for the requested model_class.** (1 connections) — `src/hal0/providers/comfyui_workflows.py`
- **Read a workflow JSON from the package resources.      ``importlib.resources`` ke** (1 connections) — `src/hal0/providers/comfyui_workflows.py`

## Relationships

- [build_workflow](build_workflow.md) (9 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/providers/comfyui_workflows.py`

## Audit Trail

- EXTRACTED: 48 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*