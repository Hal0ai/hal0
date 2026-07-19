# TestClient

> 10 nodes

## Key Concepts

- **TestClient** (5 connections)
- **isolated_client()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_get_apply_plan_returns_full_registry()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_put_settings_response_includes_apply_plan()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_put_settings_apply_plan_flattens_nested_keys()** (3 connections) — `tests/api/test_settings_apply.py`
- **test_put_settings_response_preserves_existing_top_level_shape()** (3 connections) — `tests/api/test_settings_apply.py`
- **The dashboard fetches this once on mount. The shape has to     carry every key t** (1 connections) — `tests/api/test_settings_apply.py`
- **The PUT response carries ``_hal0.apply_plan`` so the success     toast can rende** (1 connections) — `tests/api/test_settings_apply.py`
- **A nested body touching a service-restart key surfaces that key in     the plan's** (1 connections) — `tests/api/test_settings_apply.py`
- **The existing PUT contract (response is the merged config     dict at the top lev** (1 connections) — `tests/api/test_settings_apply.py`

## Relationships

- [test_settings_apply.py](test_settings_apply.py.md) (5 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_settings_apply.py`

## Audit Trail

- EXTRACTED: 23 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*