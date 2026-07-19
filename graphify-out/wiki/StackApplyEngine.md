# StackApplyEngine

> 60 nodes · cohesion 0.07

## Key Concepts

- **StackApplyEngine** (61 connections) — `src/hal0/stacks/apply.py`
- **StackChangePlan** (13 connections) — `src/hal0/stacks/apply.py`
- **.plan()** (12 connections) — `src/hal0/stacks/apply.py`
- **ConvergeReport** (9 connections) — `src/hal0/stacks/apply.py`
- **Any** (9 connections)
- **_stack()** (9 connections) — `tests/stacks/test_apply_plan.py`
- **TestGuardedReconcile** (9 connections) — `tests/stacks/test_apply_plan.py`
- **_write_agent_slot()** (9 connections) — `tests/stacks/test_apply_plan.py`
- **test_apply_plan.py** (8 connections) — `tests/stacks/test_apply_plan.py`
- **TestReconciliation** (8 connections) — `tests/stacks/test_apply_plan.py`
- **.converge()** (7 connections) — `src/hal0/stacks/apply.py`
- **.drift_status()** (7 connections) — `src/hal0/stacks/apply.py`
- **._projection_live()** (7 connections) — `src/hal0/stacks/apply.py`
- **._reconciled_stack_slot()** (7 connections) — `src/hal0/stacks/apply.py`
- **_read_toml_or_none()** (6 connections) — `src/hal0/stacks/apply.py`
- **._converge_primary()** (6 connections) — `src/hal0/stacks/apply.py`
- **_slots_dir()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **.test_conflicting_device_profile_is_flagged_not_applied()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **.test_device_flip_repoints_stale_profile()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **.test_second_npu_anchor_is_flagged()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **._write_slot()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **.test_plan_writes_nothing()** (6 connections) — `tests/stacks/test_apply_plan.py`
- **apply.py** (5 connections) — `src/hal0/stacks/apply.py`
- **._converge_capabilities()** (5 connections) — `src/hal0/stacks/apply.py`
- **._converge_unload()** (5 connections) — `src/hal0/stacks/apply.py`
- *... and 35 more nodes in this community*

## Relationships

- [StackConfig](StackConfig.md) (23 shared connections)
- [read_stack_state](read_stack_state.md) (7 shared connections)
- [test_drift.py](test_drift.py.md) (7 shared connections)
- [stacks.py](stacks.py.md) (5 shared connections)
- [test_apply_commit.py](test_apply_commit.py.md) (5 shared connections)
- [SlotConfigError](SlotConfigError.md) (4 shared connections)
- [NpuExclusivityViolation](NpuExclusivityViolation.md) (4 shared connections)
- [SlotConfigStore](SlotConfigStore.md) (4 shared connections)
- [SlotState](SlotState.md) (3 shared connections)
- [FakeSnap](FakeSnap.md) (3 shared connections)
- [.apply](apply.md) (1 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)

## Source Files

- `src/hal0/stacks/apply.py`
- `tests/stacks/test_apply_plan.py`

## Audit Trail

- EXTRACTED: 243 (75%)
- INFERRED: 82 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*