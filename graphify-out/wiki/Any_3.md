# Any

> 16 nodes · cohesion 0.12

## Key Concepts

- **Any** (44 connections)
- **test_delete_forwards_force_query_param()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_legacy_fields_still_present()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_lifespan_hydrate_keeps_explicit_hal0_upstream()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_omits_labels_when_none_declared()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_state_stream_404_on_unknown_slot()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_json_serialisation_roundtrips()** (5 connections) — `tests/api/test_slots_routes.py`
- **container_stub()** (3 connections) — `tests/api/test_slots_routes.py`
- **.__init__()** (2 connections) — `tests/api/test_slots_routes.py`
- **v0.1.x clients consuming /api/slots see every legacy key unchanged.** (1 connections) — `tests/api/test_slots_routes.py`
- **Labels list is omitted (not empty) when the slot config has no     ``model.label** (1 connections) — `tests/api/test_slots_routes.py`
- **The enriched body must be valid JSON (no exotic types leaked).** (1 connections) — `tests/api/test_slots_routes.py`
- **An explicit upstreams.toml entry for ``hal0`` survives startup unchanged.      O** (1 connections) — `tests/api/test_slots_routes.py`
- **Patch ``container_provider()`` with a stateful fake; yield its state.      The f** (1 connections) — `tests/api/test_slots_routes.py`
- **DELETE ?force=true binds + forwards to the manager and echoes ``forced``.      '** (1 connections) — `tests/api/test_slots_routes.py`
- **The SSE endpoint must 404 on an unknown slot (Team I gap #2).      Per the gap b** (1 connections) — `tests/api/test_slots_routes.py`

## Relationships

- [.json](json.md) (22 shared connections)
- [TestClient](TestClient.md) (22 shared connections)
- [test_slots_routes.py](test_slots_routes.py.md) (8 shared connections)
- [FastAPI](FastAPI.md) (8 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 90 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*