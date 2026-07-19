# perms.py

> 21 nodes

## Key Concepts

- **perms.py** (15 connections) — `src/hal0/install/perms.py`
- **PermRow** (13 connections) — `src/hal0/install/perms.py`
- **plan()** (9 connections) — `src/hal0/install/perms.py`
- **observe()** (6 connections) — `src/hal0/install/perms.py`
- **Path** (6 connections)
- **_expand_row()** (6 connections) — `src/hal0/install/perms.py`
- **_child_mode_for()** (5 connections) — `src/hal0/install/perms.py`
- **commit()** (5 connections) — `src/hal0/install/perms.py`
- **ownership_table()** (4 connections) — `src/hal0/install/perms.py`
- **_apply_one()** (4 connections) — `src/hal0/install/perms.py`
- **_owner_name()** (2 connections) — `src/hal0/install/perms.py`
- **_group_name()** (2 connections) — `src/hal0/install/perms.py`
- **OwnershipStore — one declarative truth for filesystem ownership + mode.  hal0's** (1 connections) — `src/hal0/install/perms.py`
- **One path's declared ownership + mode.      ``mode`` is the permission bits only** (1 connections) — `src/hal0/install/perms.py`
- **THE single source of truth for hal0 path ownership.      ``service_user="hal0"``** (1 connections) — `src/hal0/install/perms.py`
- **Snapshot one path's ownership + permission bits, or absence.** (1 connections) — `src/hal0/install/perms.py`
- **Resolve the effective mode for one glob-matched child.      Directories always u** (1 connections) — `src/hal0/install/perms.py`
- **Expand a glob row to one (path, row) per match; identity for plain rows.      No** (1 connections) — `src/hal0/install/perms.py`
- **Snapshot disk and compute the per-path ownership diff. Writes NOTHING.      ``ob** (1 connections) — `src/hal0/install/perms.py`
- **Resolve owner/group to ids and apply chown + chmod to one path.** (1 connections) — `src/hal0/install/perms.py`
- **Apply every drifted diff, rolling back on failure. Returns paths changed.      M** (1 connections) — `src/hal0/install/perms.py`

## Relationships

- [test_perms.py](test_perms.py.md) (8 shared connections)
- [OwnershipPlan](OwnershipPlan.md) (6 shared connections)
- [useSlots.ts](useSlots.ts.md) (1 shared connections)
- [_by_target](_by_target.md) (1 shared connections)

## Source Files

- `src/hal0/install/perms.py`

## Audit Trail

- EXTRACTED: 86 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*