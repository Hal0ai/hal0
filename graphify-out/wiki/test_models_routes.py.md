# test_models_routes.py

> 27 nodes · cohesion 0.11

## Key Concepts

- **test_models_routes.py** (35 connections) — `tests/api/test_models_routes.py`
- **TestClient** (22 connections)
- **test_disabled_or_filtered_remote_curates_api_models()** (5 connections) — `tests/api/test_models_routes.py`
- **inspect_app()** (4 connections) — `tests/api/test_models_routes.py`
- **test_list_models_surfaces_installed_flm_models()** (4 connections) — `tests/api/test_models_routes.py`
- **test_slot_backed_upstreams_never_stamp_origin_upstream()** (4 connections) — `tests/api/test_models_routes.py`
- **inspect_client()** (3 connections) — `tests/api/test_models_routes.py`
- **FastAPI** (3 connections)
- **test_get_model_attaches_ns()** (3 connections) — `tests/api/test_models_routes.py`
- **test_inspect_bad_json_returns_400()** (3 connections) — `tests/api/test_models_routes.py`
- **test_inspect_rejects_missing_repo_input()** (3 connections) — `tests/api/test_models_routes.py`
- **test_inspect_rejects_non_org_name_input()** (3 connections) — `tests/api/test_models_routes.py`
- **test_list_models_attaches_ns_for_registry_entries()** (3 connections) — `tests/api/test_models_routes.py`
- **test_list_models_type_is_dispatcher_vocab_for_local_rows()** (3 connections) — `tests/api/test_models_routes.py`
- **test_update_model_rejects_managed_args_in_extra_args()** (3 connections) — `tests/api/test_models_routes.py`
- **Tests for the /api/models surface added in the v3 wireup.  Covers two pieces:** (1 connections) — `tests/api/test_models_routes.py`
- **GET /api/models/{id} carries the same ``ns`` derivation.** (1 connections) — `tests/api/test_models_routes.py`
- **Local registry rows expose ``type`` in the DISPATCHER vocabulary     (llm/embedd** (1 connections) — `tests/api/test_models_routes.py`
- **Either ``hf_repo`` or ``hf_url`` must be present + non-empty.** (1 connections) — `tests/api/test_models_routes.py`
- **Single-token inputs like 'qwen' are rejected as not org/name.** (1 connections) — `tests/api/test_models_routes.py`
- **Non-JSON bodies are rejected with the validation envelope.** (1 connections) — `tests/api/test_models_routes.py`
- **Installed FLM models appear in /api/models as npu models so the NPU slot     pic** (1 connections) — `tests/api/test_models_routes.py`
- **The composite ``hal0`` aggregate and container slots serve LOCAL     models — th** (1 connections) — `tests/api/test_models_routes.py`
- **/api/models honors enabled + advertise_models + model_filters for     remote ups** (1 connections) — `tests/api/test_models_routes.py`
- **Save-time §21.7 screen: a managed flag in defaults.extra_args fails the     PUT** (1 connections) — `tests/api/test_models_routes.py`
- *... and 2 more nodes in this community*

## Relationships

- [_hf_handler](_hf_handler.md) (25 shared connections)
- [_derive_ns](_derive_ns.md) (5 shared connections)
- [screen_extra_args_json](screen_extra_args_json.md) (3 shared connections)
- [Upstream](Upstream.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [ModelFilters](ModelFilters.md) (1 shared connections)

## Source Files

- `tests/api/test_models_routes.py`

## Audit Trail

- EXTRACTED: 110 (97%)
- INFERRED: 3 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*