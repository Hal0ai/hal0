# perms.py

> 24 nodes · cohesion 0.13

## Key Concepts

- **perms.py** (15 connections) — `src/hal0/install/perms.py`
- **plan()** (9 connections) — `src/hal0/install/perms.py`
- **OwnershipPlan** (7 connections) — `src/hal0/install/perms.py`
- **observe()** (6 connections) — `src/hal0/install/perms.py`
- **Path** (6 connections)
- **commit()** (5 connections) — `src/hal0/install/perms.py`
- **PermDiff** (5 connections) — `src/hal0/install/perms.py`
- **_apply_one()** (4 connections) — `src/hal0/install/perms.py`
- **ownership_table()** (4 connections) — `src/hal0/install/perms.py`
- **audit_rows()** (3 connections) — `src/hal0/install/perms.py`
- **_group_name()** (2 connections) — `src/hal0/install/perms.py`
- **_owner_name()** (2 connections) — `src/hal0/install/perms.py`
- **.drifted()** (2 connections) — `src/hal0/install/perms.py`
- **.changed()** (1 connections) — `src/hal0/install/perms.py`
- **.changed()** (1 connections) — `src/hal0/install/perms.py`
- **OwnershipStore — one declarative truth for filesystem ownership + mode.  hal0's** (1 connections) — `src/hal0/install/perms.py`
- **THE single source of truth for hal0 path ownership.      ``service_user="hal0"``** (1 connections) — `src/hal0/install/perms.py`
- **Snapshot one path's ownership + permission bits, or absence.** (1 connections) — `src/hal0/install/perms.py`
- **The declared target for one concrete path, plus its current observation.      ``** (1 connections) — `src/hal0/install/perms.py`
- **Compute-only result of planning the ownership table against disk.      ``diffs``** (1 connections) — `src/hal0/install/perms.py`
- **Snapshot disk and compute the per-path ownership diff. Writes NOTHING.      ``ob** (1 connections) — `src/hal0/install/perms.py`
- **Resolve owner/group to ids and apply chown + chmod to one path.** (1 connections) — `src/hal0/install/perms.py`
- **Apply every drifted diff, rolling back on failure. Returns paths changed.      M** (1 connections) — `src/hal0/install/perms.py`
- **Render an :class:`OwnershipPlan` as ``doctor``-style audit rows.      Uses the s** (1 connections) — `src/hal0/install/perms.py`

## Relationships

- [PermRow](PermRow.md) (8 shared connections)
- [test_perms.py](test_perms.py.md) (3 shared connections)

## Source Files

- `src/hal0/install/perms.py`

## Audit Trail

- EXTRACTED: 81 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*