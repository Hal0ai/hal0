# test_slots_policy.py

> 15 nodes · cohesion 0.26

## Key Concepts

- **test_slots_policy.py** (11 connections) — `tests/api/test_slots_policy.py`
- **_normalize_create_body()** (8 connections) — `src/hal0/api/routes/slots.py`
- **FastAPI** (6 connections)
- **_seed_slot()** (5 connections) — `tests/api/test_slots_policy.py`
- **TestClient** (4 connections)
- **test_capacity_endpoint_reports_slot_budget()** (4 connections) — `tests/api/test_slots_policy.py`
- **test_create_slot_allows_when_under_budget()** (4 connections) — `tests/api/test_slots_policy.py`
- **test_create_slot_rejects_when_max_slots_reached()** (4 connections) — `tests/api/test_slots_policy.py`
- **isolated_app()** (3 connections) — `tests/api/test_slots_policy.py`
- **isolated_client()** (3 connections) — `tests/api/test_slots_policy.py`
- **test_next_free_port_honours_configured_range()** (3 connections) — `tests/api/test_slots_policy.py`
- **test_normalize_create_body_allocates_from_configured_pool()** (2 connections) — `tests/api/test_slots_policy.py`
- **test_normalize_create_body_keeps_explicit_port()** (2 connections) — `tests/api/test_slots_policy.py`
- **Normalize a POST /api/slots body to the canonical nested shape.      Two compat** (1 connections) — `src/hal0/api/routes/slots.py`
- **[slots] policy wiring — max_slots creation gate + configurable port pool.  Both** (1 connections) — `tests/api/test_slots_policy.py`

## Relationships

- [slots.py](slots.py.md) (3 shared connections)
- [test_slot_config_validation.py](test_slot_config_validation.py.md) (2 shared connections)
- [stacks.py](stacks.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/slots.py`
- `tests/api/test_slots_policy.py`

## Audit Trail

- EXTRACTED: 53 (87%)
- INFERRED: 8 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*