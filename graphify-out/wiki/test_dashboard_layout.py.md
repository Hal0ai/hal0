# test_dashboard_layout.py

> 25 nodes · cohesion 0.11

## Key Concepts

- **test_dashboard_layout.py** (12 connections) — `tests/api/test_dashboard_layout.py`
- **TestClient** (11 connections)
- **layout_client()** (4 connections) — `tests/api/test_dashboard_layout.py`
- **test_get_no_file_returns_empty()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_pin_keys_in_order_allowed()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_put_unknown_card_id_in_enabled_returns_422()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_put_unknown_key_in_order_returns_422()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_put_valid_then_get_returns_layout()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_put_wrong_version_returns_422()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_reconcile_pinned_slot_gets_pin_key()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_reconcile_span_clamped()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_reconcile_stale_pin_dropped()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **test_round_trip_persistence()** (3 connections) — `tests/api/test_dashboard_layout.py`
- **Tests for GET/PUT /api/user/dashboard-layout.  Uses ``tmp_hal0_home`` so the lay** (1 connections) — `tests/api/test_dashboard_layout.py`
- **Pinned slot name gets a pin:<name> inserted into order after 'slots'.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **pin:<name> key in order/spans is dropped when not in pinned and no live slot.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **spans values outside [1,12] are clamped on save and GET.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **pin:<anything> keys are accepted in order (not rejected as unknown).** (1 connections) — `tests/api/test_dashboard_layout.py`
- **TestClient isolated under tmp_hal0_home so layout writes go to tmp.      Mounts** (1 connections) — `tests/api/test_dashboard_layout.py`
- **GET with no saved layout returns 200 {}.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **PUT valid layout -> 204; subsequent GET returns it (reconciled).** (1 connections) — `tests/api/test_dashboard_layout.py`
- **PUT with an unknown card id in enabled -> 422 layout.invalid.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **PUT with an unknown (non-pin) key in order -> 422.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **PUT with v != 2 -> 422.** (1 connections) — `tests/api/test_dashboard_layout.py`
- **Layout saved on PUT is returned on two successive GETs (persists).** (1 connections) — `tests/api/test_dashboard_layout.py`

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_dashboard_layout.py`

## Audit Trail

- EXTRACTED: 68 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*