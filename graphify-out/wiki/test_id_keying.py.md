# test_id_keying.py

> 26 nodes · cohesion 0.20

## Key Concepts

- **test_id_keying.py** (17 connections) — `tests/slots/test_id_keying.py`
- **_manager()** (16 connections) — `tests/slots/test_id_keying.py`
- **Path** (14 connections)
- **_create()** (13 connections) — `tests/slots/test_id_keying.py`
- **SlotManager** (5 connections)
- **test_bare_manager_unchanged()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_fold_identity_populates_rows_and_claims()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_inmemory_dicts_keyed_by_durable_slot_id()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_rename_does_not_rekey_caches()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_rename_rejects_name_collision()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_rename_rejects_running_slot()** (5 connections) — `tests/slots/test_id_keying.py`
- **test_create_assigns_id_and_acquires_port()** (4 connections) — `tests/slots/test_id_keying.py`
- **test_delete_releases_port_and_id()** (4 connections) — `tests/slots/test_id_keying.py`
- **test_rename_is_pure_relabel_preserving_id()** (4 connections) — `tests/slots/test_id_keying.py`
- **test_second_slot_never_double_claims_same_port()** (4 connections) — `tests/slots/test_id_keying.py`
- **test_slot_id_to_name_roundtrip()** (4 connections) — `tests/slots/test_id_keying.py`
- **test_surrogate_rebinds_to_durable_id_when_row_appears()** (4 connections) — `tests/slots/test_id_keying.py`
- **hal0_home()** (3 connections) — `tests/slots/test_id_keying.py`
- **test_bare_manager_key_is_negative_surrogate_bijection()** (3 connections) — `tests/slots/test_id_keying.py`
- **MonkeyPatch** (1 connections)
- **SlotManager + SlotIdentityStore/PortAuthority wiring (rework §11.1/§11.2).  Exer** (1 connections) — `tests/slots/test_id_keying.py`
- **No injected stores → no id, no authority (legacy behaviour intact).** (1 connections) — `tests/slots/test_id_keying.py`
- **With an identity store wired, every per-slot cache is keyed by the     durable `** (1 connections) — `tests/slots/test_id_keying.py`
- **No identity store → ``_key`` mints stable negative surrogates that can     never** (1 connections) — `tests/slots/test_id_keying.py`
- **A name touched BEFORE its identity row exists (the boot ordering where     ``rec** (1 connections) — `tests/slots/test_id_keying.py`
- *... and 1 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [PortAuthority](PortAuthority.md) (1 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (1 shared connections)

## Source Files

- `tests/slots/test_id_keying.py`

## Audit Trail

- EXTRACTED: 128 (97%)
- INFERRED: 4 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*