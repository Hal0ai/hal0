# _stores

> 22 nodes · cohesion 0.29

## Key Concepts

- **_stores()** (20 connections) — `tests/ports/test_authority.py`
- **test_authority.py** (19 connections) — `tests/ports/test_authority.py`
- **Path** (17 connections)
- **_slot()** (16 connections) — `tests/ports/test_authority.py`
- **test_double_claim_rejected_under_concurrency()** (5 connections) — `tests/ports/test_authority.py`
- **test_pool_exhaustion_raises()** (5 connections) — `tests/ports/test_authority.py`
- **test_acquire_grants_and_binds()** (4 connections) — `tests/ports/test_authority.py`
- **test_acquire_honours_preferred_when_free()** (4 connections) — `tests/ports/test_authority.py`
- **test_acquire_lowest_free()** (4 connections) — `tests/ports/test_authority.py`
- **test_acquire_skips_preferred_when_taken()** (4 connections) — `tests/ports/test_authority.py`
- **test_coresident_shadow_shares_anchor_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_delete_slot_then_release_frees_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_no_two_live_claims_on_one_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_reallocate_moves_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_reconcile_releases_orphaned_claim()** (4 connections) — `tests/ports/test_authority.py`
- **test_release_frees_the_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_release_then_reacquire_same_port()** (4 connections) — `tests/ports/test_authority.py`
- **test_reserve_blocks_allocation()** (4 connections) — `tests/ports/test_authority.py`
- **test_reserve_conflict_different_owner()** (3 connections) — `tests/ports/test_authority.py`
- **test_reserve_is_idempotent_same_label()** (3 connections) — `tests/ports/test_authority.py`
- **PortAuthority (rework §11.2) — the single writer of port ownership.  Pins: alloc** (1 connections) — `tests/ports/test_authority.py`
- **N threads each acquire for a distinct slot at once. The partial     unique index** (1 connections) — `tests/ports/test_authority.py`

## Relationships

- [SlotIdentityStore](SlotIdentityStore.md) (2 shared connections)
- [PortAuthority](PortAuthority.md) (1 shared connections)
- [authority.py](authority.py.md) (1 shared connections)

## Source Files

- `tests/ports/test_authority.py`

## Audit Trail

- EXTRACTED: 136 (99%)
- INFERRED: 2 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*