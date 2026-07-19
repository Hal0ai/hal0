# ModelVariant

> 36 nodes

## Key Concepts

- **ModelVariant** (14 connections) — `src/hal0/comfyui/capabilities.py`
- **default_variant()** (11 connections) — `src/hal0/comfyui/capabilities.py`
- **variant_for()** (10 connections) — `src/hal0/comfyui/selection.py`
- **test_capabilities.py** (9 connections) — `tests/comfyui/test_capabilities.py`
- **auto_selections()** (8 connections) — `src/hal0/comfyui/selection.py`
- **test_selection.py** (8 connections) — `tests/comfyui/test_selection.py`
- **capabilities.py** (4 connections) — `src/hal0/comfyui/capabilities.py`
- **selection.py** (3 connections) — `src/hal0/comfyui/selection.py`
- **test_auto_selections_count()** (3 connections) — `tests/comfyui/test_selection.py`
- **test_auto_selections_are_model_variants()** (3 connections) — `tests/comfyui/test_selection.py`
- **test_auto_selections_match_defaults()** (3 connections) — `tests/comfyui/test_selection.py`
- **test_variant_for_known()** (3 connections) — `tests/comfyui/test_selection.py`
- **test_variant_for_default_family()** (3 connections) — `tests/comfyui/test_selection.py`
- **test_variant_for_unknown_family_raises()** (3 connections) — `tests/comfyui/test_selection.py`
- **Capability** (2 connections) — `src/hal0/comfyui/capabilities.py`
- **test_default_variant_est_seconds()** (2 connections) — `tests/comfyui/test_capabilities.py`
- **test_ltx2_default_txt2video()** (2 connections) — `tests/comfyui/test_capabilities.py`
- **test_ltx2_default_img2video()** (2 connections) — `tests/comfyui/test_capabilities.py`
- **test_default_variant_is_first_alternative()** (2 connections) — `tests/comfyui/test_capabilities.py`
- **test_variant_for_unknown_capability_raises()** (2 connections) — `tests/comfyui/test_selection.py`
- **ComfyUI capability registry — Task 2.2.** (1 connections) — `src/hal0/comfyui/capabilities.py`
- **Return the default (first) variant for a capability id or Capability.** (1 connections) — `src/hal0/comfyui/capabilities.py`
- **Task 3.5: ComfyUI model selection helpers.  Public API:     auto_selections() ->** (1 connections) — `src/hal0/comfyui/selection.py`
- **Return the default ModelVariant for every capability in CAPABILITIES order.** (1 connections) — `src/hal0/comfyui/selection.py`
- **Return the ModelVariant with *family* from the named capability.      Raises:** (1 connections) — `src/hal0/comfyui/selection.py`
- *... and 11 more nodes in this community*

## Relationships

- [orchestrate_models](orchestrate_models.md) (4 shared connections)
- [provision_comfyui_downloads](provision_comfyui_downloads.md) (4 shared connections)
- [comfyui.py](comfyui.py.md) (2 shared connections)
- [fetch_model](fetch_model.md) (1 shared connections)
- [fetch.py](fetch.py.md) (1 shared connections)
- [OrchestrationResult](OrchestrationResult.md) (1 shared connections)
- [test_fetch_ws_g.py](test_fetch_ws_g.py.md) (1 shared connections)
- [KeyError](KeyError.md) (1 shared connections)

## Source Files

- `src/hal0/comfyui/capabilities.py`
- `src/hal0/comfyui/selection.py`
- `tests/comfyui/test_capabilities.py`
- `tests/comfyui/test_selection.py`

## Audit Trail

- EXTRACTED: 78 (69%)
- INFERRED: 35 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*