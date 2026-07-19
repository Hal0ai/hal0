# .json

> 36 nodes

## Key Concepts

- **.json()** (41 connections) — `tests/api/test_slots_routes.py`
- **Path** (34 connections)
- **test_lifespan_hydrate_keeps_explicit_hal0_upstream()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_load_success_path_dispatches_via_container()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_load_unknown_slot_returns_typed_envelope()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_disable_running_slot_stops_it()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_delete_forwards_force_query_param()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_state_endpoint_returns_lifecycle_snapshot()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_slot_includes_config_enrichment()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_slot_includes_config_drift_when_requested()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_legacy_fields_still_present()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_degrades_when_container_probe_fails()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_declared_backend_flm_for_npu_device()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_get_config_unknown_slot_returns_404()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_get_config_invalid_toml_still_returns_400()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_disable_offline_slot_does_not_unload()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_patch_defaults_preserves_model_default()** (5 connections) — `tests/api/test_slots_routes.py`
- **test_patch_defaults_canonicalizes_ctx_size_key()** (5 connections) — `tests/api/test_slots_routes.py`
- **slot_root()** (3 connections) — `tests/api/test_slots_routes.py`
- **Write a chat.toml in the HAL0_HOME slot config dir.** (1 connections) — `tests/api/test_slots_routes.py`
- **An explicit upstreams.toml entry for ``hal0`` survives startup unchanged.      O** (1 connections) — `tests/api/test_slots_routes.py`
- **POST /api/slots/chat/load goes through ContainerProvider.load_sync.** (1 connections) — `tests/api/test_slots_routes.py`
- **Loading a slot with no TOML returns the typed slot.not_found envelope.** (1 connections) — `tests/api/test_slots_routes.py`
- **GET /api/slots/doesntexist/config → 404 slot.not_found.      Pre-issue-#35 the r** (1 connections) — `tests/api/test_slots_routes.py`
- **An EXISTING slot with malformed TOML still surfaces 400 slot.config_error.** (1 connections) — `tests/api/test_slots_routes.py`
- *... and 11 more nodes in this community*

## Relationships

- [TestClient](TestClient.md) (34 shared connections)
- [Any](Any.md) (24 shared connections)
- [test_slots_routes.py](test_slots_routes.py.md) (18 shared connections)
- [FastAPI](FastAPI.md) (12 shared connections)
- [create_app](create_app.md) (1 shared connections)
- [load_manifest](load_manifest.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 184 (99%)
- INFERRED: 2 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*