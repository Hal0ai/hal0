# test_v1_models_filters.py

> 25 nodes · cohesion 0.20

## Key Concepts

- **test_v1_models_filters.py** (17 connections) — `tests/api/test_v1_models_filters.py`
- **_seed_remote()** (17 connections) — `tests/api/test_v1_models_filters.py`
- **TestClient** (16 connections)
- **_listed_ids()** (10 connections) — `tests/api/test_v1_models_filters.py`
- **test_default_hides_media_modalities_keeps_text()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_empty_filters_pass_all()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_exclude_overrides_include()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_get_model_by_id_bypasses_default_modality_filter()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_include_globs_curate_the_catalog()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_show_all_reveals_hidden_media_modalities()** (5 connections) — `tests/api/test_v1_models_filters.py`
- **test_disabled_upstream_contributes_nothing()** (4 connections) — `tests/api/test_v1_models_filters.py`
- **test_get_model_by_id_still_honors_owned_by()** (4 connections) — `tests/api/test_v1_models_filters.py`
- **test_no_filter_advertises_everything()** (4 connections) — `tests/api/test_v1_models_filters.py`
- **test_registry_detail_folds_into_catalog_row()** (4 connections) — `tests/api/test_v1_models_filters.py`
- **test_show_all_query_param_is_case_and_value_tolerant()** (4 connections) — `tests/api/test_v1_models_filters.py`
- **test_model_filter_header_matches_query_param()** (3 connections) — `tests/api/test_v1_models_filters.py`
- **test_owned_by_query_filters_out_passthroughs()** (3 connections) — `tests/api/test_v1_models_filters.py`
- **test_owned_by_query_keeps_matching_owner()** (3 connections) — `tests/api/test_v1_models_filters.py`
- **Integration tests — per-upstream model filters + enabled flag on /v1/models.  Sp** (1 connections) — `tests/api/test_v1_models_filters.py`
- **A raw catalog id that resolves in the local model registry surfaces     labels/c** (1 connections) — `tests/api/test_v1_models_filters.py`
- **The default (show_all=false) view hides image-gen / TTS / ASR raw     catalog id** (1 connections) — `tests/api/test_v1_models_filters.py`
- **``?show_all=true`` restores the media-gen/audio rows the default     view hides** (1 connections) — `tests/api/test_v1_models_filters.py`
- **A handful of truthy spellings all enable show_all; any other value     (includin** (1 connections) — `tests/api/test_v1_models_filters.py`
- **GET /v1/models/{id} is an explicit fetch — a valid non-text id     (hidden from** (1 connections) — `tests/api/test_v1_models_filters.py`
- **The by-id bypass is modality-only — the ``owned_by`` curation still     scopes a** (1 connections) — `tests/api/test_v1_models_filters.py`

## Relationships

- [ModelFilters](ModelFilters.md) (3 shared connections)
- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `tests/api/test_v1_models_filters.py`

## Audit Trail

- EXTRACTED: 124 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*