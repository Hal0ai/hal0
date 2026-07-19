# Path

> 17 nodes · cohesion 0.24

## Key Concepts

- **Path** (9 connections)
- **FakeContainerProvider** (8 connections)
- **test_upstream_reconcile.py** (7 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_load_on_ready_trio_shadow_does_not_register()** (6 connections) — `tests/slots/test_upstream_reconcile.py`
- **TestReconcileContainerUpstreams** (6 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_skips_trio_shadow()** (6 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_load_on_ready_slot_restores_upstream()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **TestReconcileAdoptsOfflineButActive** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_adopts_offline_but_active_slot()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_still_skips_offline_and_inactive()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_restores_upstream_for_running_container()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_skips_container_dead_while_api_down()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **.test_skips_never_loaded_slot()** (5 connections) — `tests/slots/test_upstream_reconcile.py`
- **TestIdempotentLoadReregisters** (4 connections) — `tests/slots/test_upstream_reconcile.py`
- **_write_trio_shadow()** (4 connections) — `tests/slots/test_upstream_reconcile.py`
- **#732: upstream-registry restart drop — reconciliation + idempotent load.  Per-sl** (1 connections) — `tests/slots/test_upstream_reconcile.py`
- **Startup reconcile must adopt a running container whose state.json is     stale-O** (1 connections) — `tests/slots/test_upstream_reconcile.py`

## Relationships

- [SlotManager](SlotManager.md) (8 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (8 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (4 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)

## Source Files

- `tests/slots/test_upstream_reconcile.py`

## Audit Trail

- EXTRACTED: 68 (78%)
- INFERRED: 19 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*