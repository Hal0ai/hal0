# read_stack_state

> 23 nodes · cohesion 0.14

## Key Concepts

- **read_stack_state()** (9 connections) — `src/hal0/stacks/state.py`
- **StackStateRecord** (8 connections) — `src/hal0/stacks/state.py`
- **.record_active()** (7 connections) — `src/hal0/stacks/apply.py`
- **stack_content_hash()** (7 connections) — `src/hal0/stacks/state.py`
- **write_stack_state_atomic()** (7 connections) — `src/hal0/stacks/state.py`
- **TestStateRecord** (6 connections) — `tests/stacks/test_drift.py`
- **state.py** (5 connections) — `src/hal0/stacks/state.py`
- **TestContentHash** (5 connections) — `tests/stacks/test_drift.py`
- **.test_round_trip()** (4 connections) — `tests/stacks/test_drift.py`
- **Any** (3 connections)
- **.from_dict()** (3 connections) — `src/hal0/stacks/state.py`
- **.to_dict()** (3 connections) — `src/hal0/stacks/state.py`
- **Path** (2 connections)
- **.test_changes_with_content()** (2 connections) — `tests/stacks/test_drift.py`
- **.test_stable_and_order_independent()** (2 connections) — `tests/stacks/test_drift.py`
- **.test_corrupt_state_returns_none()** (2 connections) — `tests/stacks/test_drift.py`
- **.test_read_missing_returns_none()** (2 connections) — `tests/stacks/test_drift.py`
- **Record ``plan``'s stack as active, fingerprinting what it wrote.          Call A** (1 connections) — `src/hal0/stacks/apply.py`
- **Active-stack pointer + content hashing for drift detection (spec §7).  Mirrors t** (1 connections) — `src/hal0/stacks/state.py`
- **Which stack is applied, the hash of what it wrote, and whether converge     brou** (1 connections) — `src/hal0/stacks/state.py`
- **sha256 over the canonical slot→TOML-dict projection.      Canonical serializatio** (1 connections) — `src/hal0/stacks/state.py`
- **Persist the active-stack pointer atomically (tmpfile + fsync + replace).** (1 connections) — `src/hal0/stacks/state.py`
- **Read the active-stack pointer, or ``None`` when absent or corrupt.      A missin** (1 connections) — `src/hal0/stacks/state.py`

## Relationships

- [StackApplyEngine](StackApplyEngine.md) (7 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [test_drift.py](test_drift.py.md) (2 shared connections)

## Source Files

- `src/hal0/stacks/apply.py`
- `src/hal0/stacks/state.py`
- `tests/stacks/test_drift.py`

## Audit Trail

- EXTRACTED: 57 (69%)
- INFERRED: 26 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*