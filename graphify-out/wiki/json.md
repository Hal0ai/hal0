# .json

> 36 nodes · cohesion 0.08

## Key Concepts

- **.json()** (41 connections) — `tests/api/test_slots_routes.py`
- **Path** (34 connections)
- **test_disable_running_slot_stops_it()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_slot_includes_config_drift_when_requested()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_slot_includes_config_enrichment()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_invalid_enable_surfaces_conflict()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_degrades_when_container_probe_fails()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_declared_backend_flm_for_npu_device()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_emits_coresident_group_for_npu_trio()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_emits_type_and_model_default_for_persona_dropdown()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_load_success_path_dispatches_via_container()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_load_unknown_slot_returns_typed_envelope()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_state_endpoint_returns_lifecycle_snapshot()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_unload_after_load_transitions_to_offline()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_config_invalid_toml_still_returns_400()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_get_config_unknown_slot_returns_404()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_patch_defaults_canonicalizes_ctx_size_key()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_patch_defaults_preserves_model_default()** (5 connections) — `tests/api/test_slots_routes.py`
- **slot_root()** (3 connections) — `tests/api/test_slots_routes.py`
- **When NPU LLM anchor is enabled, all three trio slots get coresident_group.** (1 connections) — `tests/api/test_slots_routes.py`
- **GET /api/slots/{name} is enriched same shape as the list endpoint.** (1 connections) — `tests/api/test_slots_routes.py`
- **#863: single-slot status includes argv drift without burdening list().** (1 connections) — `tests/api/test_slots_routes.py`
- **PR-18: each entry carries ``type`` + ``model_default`` + ``enabled``.      The d** (1 connections) — `tests/api/test_slots_routes.py`
- **A failing container health probe doesn't break /api/slots.      The enrichment s** (1 connections) — `tests/api/test_slots_routes.py`
- **device=npu slots surface declared_backend='flm' (the FLM recipe).** (1 connections) — `tests/api/test_slots_routes.py`
- *... and 11 more nodes in this community*

## Relationships

- [TestClient](TestClient.md) (34 shared connections)
- [Any](Any.md) (22 shared connections)
- [test_slots_routes.py](test_slots_routes.py.md) (18 shared connections)
- [FastAPI](FastAPI.md) (12 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 186 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*