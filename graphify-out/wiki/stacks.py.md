# stacks.py

> 41 nodes

## Key Concepts

- **stacks.py** (22 connections) — `src/hal0/api/routes/stacks.py`
- **apply_stack()** (13 connections) — `src/hal0/api/routes/stacks.py`
- **Any** (12 connections)
- **_stack_to_dict()** (10 connections) — `src/hal0/api/routes/stacks.py`
- **snapshot_stack()** (10 connections) — `src/hal0/api/routes/stacks.py`
- **_registry()** (9 connections) — `src/hal0/api/routes/stacks.py`
- **Request** (9 connections)
- **create_stack()** (8 connections) — `src/hal0/api/routes/stacks.py`
- **update_stack()** (8 connections) — `src/hal0/api/routes/stacks.py`
- **_create_missing_slots()** (7 connections) — `src/hal0/api/routes/stacks.py`
- **export_stack()** (7 connections) — `src/hal0/api/routes/stacks.py`
- **_config_of()** (6 connections) — `src/hal0/api/routes/stacks.py`
- **list_stacks()** (6 connections) — `src/hal0/api/routes/stacks.py`
- **get_stack()** (6 connections) — `src/hal0/api/routes/stacks.py`
- **StackCreateBody** (5 connections) — `src/hal0/api/routes/stacks.py`
- **SnapshotBody** (5 connections) — `src/hal0/api/routes/stacks.py`
- **_missing_slot_names()** (5 connections) — `src/hal0/api/routes/stacks.py`
- **_known_model_ids()** (5 connections) — `src/hal0/api/routes/stacks.py`
- **delete_stack()** (5 connections) — `src/hal0/api/routes/stacks.py`
- **_known_profile_names()** (4 connections) — `src/hal0/api/routes/stacks.py`
- **_diff_rows()** (4 connections) — `src/hal0/api/routes/stacks.py`
- **_slot_toml_exists()** (3 connections) — `src/hal0/api/routes/stacks.py`
- **Stack catalog + apply endpoints.  Mounted under /api/stacks (spec §8 of docs/sup** (1 connections) — `src/hal0/api/routes/stacks.py`
- **Body for POST /api/stacks — slug + the full stack body.      The ``stack`` paylo** (1 connections) — `src/hal0/api/routes/stacks.py`
- **Body for POST /api/stacks/snapshot.      With no ``slug`` the snapshot is return** (1 connections) — `src/hal0/api/routes/stacks.py`
- *... and 16 more nodes in this community*

## Relationships

- [StacksCatalog](StacksCatalog.md) (10 shared connections)
- [embed_references](embed_references.md) (5 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (5 shared connections)
- [record_action](record_action.md) (5 shared connections)
- [StackConfig](StackConfig.md) (4 shared connections)
- [BaseModel](BaseModel.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [test_slots_policy.py](test_slots_policy.py.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [snapshot_live_stack](snapshot_live_stack.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/stacks.py`

## Audit Trail

- EXTRACTED: 166 (88%)
- INFERRED: 22 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*