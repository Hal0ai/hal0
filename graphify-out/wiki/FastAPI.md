# FastAPI

> 20 nodes

## Key Concepts

- **FastAPI** (11 connections)
- **test_list_merges_real_and_synthetic()** (8 connections) — `tests/api/test_slots_routes.py`
- **test_list_real_wins_on_name_collision()** (8 connections) — `tests/api/test_slots_routes.py`
- **test_state_stream_emits_transition_event()** (7 connections) — `tests/api/test_slots_routes.py`
- **test_state_stream_subscriber_cleaned_up_on_close()** (7 connections) — `tests/api/test_slots_routes.py`
- **test_state_stream_emits_sse_event_shape()** (7 connections) — `tests/api/test_slots_routes.py`
- **test_synthetic_composite_slot_offline_when_nothing_loaded()** (7 connections) — `tests/api/test_slots_routes.py`
- **test_synthetic_composite_slot_serving_when_model_loaded()** (7 connections) — `tests/api/test_slots_routes.py`
- **test_lifespan_registers_no_pseudo_upstream_for_the_composite()** (6 connections) — `tests/api/test_slots_routes.py`
- **isolated_app()** (4 connections) — `tests/api/test_slots_routes.py`
- **isolated_client()** (3 connections) — `tests/api/test_slots_routes.py`
- **A FastAPI app whose lifespan resolves paths under tmp_hal0_home.      The shared** (1 connections) — `tests/api/test_slots_routes.py`
- **Real SlotManager entries appear alongside synthetic upstream-backed ones.** (1 connections) — `tests/api/test_slots_routes.py`
- **When a real slot and a synthetic share a name, the real one wins.** (1 connections) — `tests/api/test_slots_routes.py`
- **P2-composite rebuild: no ``hal0`` Upstream is registered at startup.      Chat d** (1 connections) — `tests/api/test_slots_routes.py`
- **Driving a state change through the manager pushes a frame to the stream.      Su** (1 connections) — `tests/api/test_slots_routes.py`
- **Closing the SSE generator deregisters the SlotManager subscriber.      The state** (1 connections) — `tests/api/test_slots_routes.py`
- **The SSE stream's first frame is the current snapshot in the expected shape.** (1 connections) — `tests/api/test_slots_routes.py`
- **The synthetic composite ``hal0`` slot must derive ``status`` from     the live l** (1 connections) — `tests/api/test_slots_routes.py`
- **Counterpart to the offline case: when a dispatchable slot holds the     catalogu** (1 connections) — `tests/api/test_slots_routes.py`

## Relationships

- [.json](json.md) (12 shared connections)
- [test_slots_routes.py](test_slots_routes.py.md) (11 shared connections)
- [TestClient](TestClient.md) (9 shared connections)
- [Any](Any.md) (8 shared connections)
- [slots.py](slots.py.md) (3 shared connections)
- [Upstream](Upstream.md) (2 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 78 (93%)
- INFERRED: 6 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*