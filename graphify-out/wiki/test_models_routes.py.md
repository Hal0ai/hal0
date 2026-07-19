# test_models_routes.py

> 55 nodes

## Key Concepts

- **test_models_routes.py** (35 connections) — `tests/api/test_models_routes.py`
- **TestClient** (22 connections)
- **_hf_handler()** (14 connections) — `tests/api/test_models_routes.py`
- **_patch_httpx_transport()** (14 connections) — `tests/api/test_models_routes.py`
- **MonkeyPatch** (13 connections)
- **test_inspect_returns_gguf_variants_sorted_by_size()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_surfaces_bare_mmproj_sidecar()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_surfaces_flm_npu_repo()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_safetensors_repo_is_not_flm()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_ignores_non_mmproj_non_gguf_files()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_accepts_hf_url_alias()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_returns_502_when_hf_unreachable()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_returns_404_when_repo_missing()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_falls_back_to_top_level_size_when_no_lfs()** (6 connections) — `tests/api/test_models_routes.py`
- **test_inspect_caches_response_for_repeated_calls()** (5 connections) — `tests/api/test_models_routes.py`
- **test_inspect_response_is_application_json()** (5 connections) — `tests/api/test_models_routes.py`
- **test_disabled_or_filtered_remote_curates_api_models()** (5 connections) — `tests/api/test_models_routes.py`
- **inspect_app()** (4 connections) — `tests/api/test_models_routes.py`
- **test_list_models_surfaces_installed_flm_models()** (4 connections) — `tests/api/test_models_routes.py`
- **test_slot_backed_upstreams_never_stamp_origin_upstream()** (4 connections) — `tests/api/test_models_routes.py`
- **FastAPI** (3 connections)
- **inspect_client()** (3 connections) — `tests/api/test_models_routes.py`
- **test_list_models_attaches_ns_for_registry_entries()** (3 connections) — `tests/api/test_models_routes.py`
- **test_get_model_attaches_ns()** (3 connections) — `tests/api/test_models_routes.py`
- **test_list_models_type_is_dispatcher_vocab_for_local_rows()** (3 connections) — `tests/api/test_models_routes.py`
- *... and 30 more nodes in this community*

## Relationships

- [_derive_ns](_derive_ns.md) (5 shared connections)
- [models_service.py](models_service.py.md) (3 shared connections)
- [Upstream](Upstream.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [ModelFilters](ModelFilters.md) (1 shared connections)

## Source Files

- `tests/api/test_models_routes.py`

## Audit Trail

- EXTRACTED: 229 (99%)
- INFERRED: 3 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*