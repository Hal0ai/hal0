# test_metrics_prometheus_route.py

> 34 nodes · cohesion 0.09

## Key Concepts

- **test_metrics_prometheus_route.py** (13 connections) — `tests/api/test_metrics_prometheus_route.py`
- **render_slot_metrics()** (10 connections) — `src/hal0/slots/metrics.py`
- **_FakeSlotManager** (6 connections) — `tests/api/test_metrics_prometheus_route.py`
- **TestClient** (6 connections)
- **_FakeSlot** (5 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_route_renders_slot_state_exposition()** (5 connections) — `tests/api/test_metrics_prometheus_route.py`
- **Any** (4 connections)
- **_RaisingSlotManager** (4 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_route_degrades_to_empty_exposition_when_list_fails()** (4 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_route_with_empty_slot_list_emits_headers_and_zero_total()** (4 connections) — `tests/api/test_metrics_prometheus_route.py`
- **metrics.py** (3 connections) — `src/hal0/slots/metrics.py`
- **_escape_label()** (3 connections) — `src/hal0/slots/metrics.py`
- **test_route_is_public()** (3 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_route_returns_prometheus_content_type()** (3 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_route_with_no_slot_manager_returns_empty_body()** (3 connections) — `tests/api/test_metrics_prometheus_route.py`
- **.__init__()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **.__init__()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **.list()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **.list()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_crashed_but_active_slot_reports_up_zero()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_health_ok_true_ready_slot_reports_up()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **test_ready_slot_without_health_flag_stays_up()** (2 connections) — `tests/api/test_metrics_prometheus_route.py`
- **Any** (1 connections)
- **Slim Prometheus exposition over slot state.  Replacement for the legacy daemon-p** (1 connections) — `src/hal0/slots/metrics.py`
- **Escape a Prometheus label value (backslash, quote, newline).** (1 connections) — `src/hal0/slots/metrics.py`
- *... and 9 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (3 shared connections)
- [health.py](health.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [test_dispatchable_ready_set_single_source.py](test_dispatchable_ready_set_single_source.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/metrics.py`
- `tests/api/test_metrics_prometheus_route.py`

## Audit Trail

- EXTRACTED: 90 (88%)
- INFERRED: 12 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*