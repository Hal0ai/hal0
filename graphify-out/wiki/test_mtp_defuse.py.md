# test_mtp_defuse.py

> 17 nodes

## Key Concepts

- **test_mtp_defuse.py** (12 connections) — `tests/slots/test_mtp_defuse.py`
- **clear_stale_mtp_overrides()** (9 connections) — `src/hal0/updater/updater.py`
- **_register()** (9 connections) — `tests/slots/test_mtp_defuse.py`
- **_slot_cfg()** (9 connections) — `tests/slots/test_mtp_defuse.py`
- **_on_disk_mtp()** (9 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_keeps_force_on_for_defaults_mtp_true_even_untagged()** (6 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_clears_force_on_for_defaults_mtp_false_even_tagged()** (6 connections) — `tests/slots/test_mtp_defuse.py`
- **test_migration_clears_only_crash_combo()** (6 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_clears_force_on_for_ineligible_model()** (5 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_keeps_force_on_for_eligible_model()** (5 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_keeps_force_off_and_auto()** (5 connections) — `tests/slots/test_mtp_defuse.py`
- **test_migration_is_idempotent()** (5 connections) — `tests/slots/test_mtp_defuse.py`
- **test_swap_defuse_leaves_unresolvable_model_alone()** (4 connections) — `tests/slots/test_mtp_defuse.py`
- **Clear crash-only ``mtp = true`` slot overrides (upgrade migration).      An expl** (1 connections) — `src/hal0/updater/updater.py`
- **MTP force-on defuse — swap path + updater migration.  A slot's explicit ``mtp =** (1 connections) — `tests/slots/test_mtp_defuse.py`
- **An explicit ModelDefaults.mtp=True is eligible even with NO registry     tag — t** (1 connections) — `tests/slots/test_mtp_defuse.py`
- **An explicit ModelDefaults.mtp=False makes the model ineligible even     though i** (1 connections) — `tests/slots/test_mtp_defuse.py`

## Relationships

- [SlotManager](SlotManager.md) (8 shared connections)
- [updater.py](updater.py.md) (3 shared connections)
- [slots_config_dir](slots_config_dir.md) (2 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [test_mtp_override.py](test_mtp_override.py.md) (1 shared connections)
- [Model](Model.md) (1 shared connections)

## Source Files

- `src/hal0/updater/updater.py`
- `tests/slots/test_mtp_defuse.py`

## Audit Trail

- EXTRACTED: 76 (81%)
- INFERRED: 18 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*