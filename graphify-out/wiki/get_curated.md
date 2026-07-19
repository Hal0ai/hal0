# get_curated

> 28 nodes

## Key Concepts

- **get_curated()** (22 connections) — `src/hal0/registry/curated.py`
- **test_curated_comfyui.py** (10 connections) — `tests/registry/test_curated_comfyui.py`
- **test_curated.py** (7 connections) — `tests/registry/test_curated.py`
- **test_curated_model_validates_required_fields()** (3 connections) — `tests/registry/test_curated.py`
- **test_memory_pipeline_default_embed_model_is_pullable()** (3 connections) — `tests/registry/test_curated.py`
- **test_catalogue_has_named_picks()** (2 connections) — `tests/registry/test_curated.py`
- **test_catalogue_entries_have_hf_coordinates()** (2 connections) — `tests/registry/test_curated.py`
- **test_get_curated_hit_and_miss()** (2 connections) — `tests/registry/test_curated.py`
- **test_lookup_index_matches_list()** (2 connections) — `tests/registry/test_curated.py`
- **test_sdxl_lightning_present()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_esrgan_4x_present()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_sdxl_lightning_model_class_image()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_esrgan_4x_model_class_image()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_sdxl_lightning_comfyui_subdir()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_esrgan_4x_comfyui_subdir()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_sdxl_lightning_capability_image()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **test_esrgan_4x_capability_image()** (2 connections) — `tests/registry/test_curated_comfyui.py`
- **Return the curated entry by id, or ``None`` if not in the catalogue.** (1 connections) — `src/hal0/registry/curated.py`
- **Tests for the curated model catalogue.** (1 connections) — `tests/registry/test_curated.py`
- **The wizard contract names these three — they must always be present.** (1 connections) — `tests/registry/test_curated.py`
- **Every entry must carry hf_repo + hf_file (the pull layer's input).      Allowed** (1 connections) — `tests/registry/test_curated.py`
- **The Pydantic model rejects missing required fields.** (1 connections) — `tests/registry/test_curated.py`
- **CURATED_BY_ID is the same set as the list.** (1 connections) — `tests/registry/test_curated.py`
- **The memory pipeline's default embed id must resolve to a real curated pull sourc** (1 connections) — `tests/registry/test_curated.py`
- **_by_id()** (1 connections) — `tests/registry/test_curated_comfyui.py`
- *... and 3 more nodes in this community*

## Relationships

- [pull_jobs.py](pull_jobs.py.md) (3 shared connections)
- [scan_and_register](scan_and_register.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [v1.py](v1.py.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)
- [orchestrate.py](orchestrate.py.md) (1 shared connections)
- [HaloaiModel](HaloaiModel.md) (1 shared connections)
- [resolve_servable_model](resolve_servable_model.md) (1 shared connections)
- [embed_references](embed_references.md) (1 shared connections)
- [test_curated_image_models.py](test_curated_image_models.py.md) (1 shared connections)
- [test_curated_pull_coords.py](test_curated_pull_coords.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/curated.py`
- `tests/registry/test_curated.py`
- `tests/registry/test_curated_comfyui.py`

## Audit Trail

- EXTRACTED: 52 (65%)
- INFERRED: 28 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*