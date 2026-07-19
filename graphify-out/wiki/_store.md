# _store

> 20 nodes · cohesion 0.24

## Key Concepts

- **_store()** (16 connections) — `tests/slots/test_identity.py`
- **test_identity.py** (15 connections) — `tests/slots/test_identity.py`
- **Path** (14 connections)
- **SlotAlreadyExists** (10 connections) — `src/hal0/slots/identity.py`
- **test_duplicate_name_rejected_on_create()** (4 connections) — `tests/slots/test_identity.py`
- **test_rename_preserves_id()** (4 connections) — `tests/slots/test_identity.py`
- **test_rename_to_taken_name_rejected()** (4 connections) — `tests/slots/test_identity.py`
- **test_coresident_group_set()** (3 connections) — `tests/slots/test_identity.py`
- **test_create_assigns_opaque_id_and_roundtrips()** (3 connections) — `tests/slots/test_identity.py`
- **test_get_missing_raises()** (3 connections) — `tests/slots/test_identity.py`
- **test_id_not_reused_after_delete()** (3 connections) — `tests/slots/test_identity.py`
- **test_link_cascades_when_parent_deleted()** (3 connections) — `tests/slots/test_identity.py`
- **test_links()** (3 connections) — `tests/slots/test_identity.py`
- **test_list_all_and_by_type()** (3 connections) — `tests/slots/test_identity.py`
- **test_rename_updates_timestamp_not_created()** (3 connections) — `tests/slots/test_identity.py`
- **test_resolve_id()** (3 connections) — `tests/slots/test_identity.py`
- **test_seed_ids()** (3 connections) — `tests/slots/test_identity.py`
- **Raised when creating/renaming to a name another slot already holds.** (1 connections) — `src/hal0/slots/identity.py`
- **SlotIdentityStore (rework §11.1) — opaque id ⇄ name bridge.  Pins the core §11.1** (1 connections) — `tests/slots/test_identity.py`
- **The load-bearing §11.1 guarantee: rename changes only the label.** (1 connections) — `tests/slots/test_identity.py`

## Relationships

- [SlotIdentityStore](SlotIdentityStore.md) (5 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/slots/identity.py`
- `tests/slots/test_identity.py`

## Audit Trail

- EXTRACTED: 93 (93%)
- INFERRED: 7 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*