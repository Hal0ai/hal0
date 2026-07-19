# rerender_slot_units

> 13 nodes

## Key Concepts

- **rerender_slot_units()** (9 connections) — `src/hal0/updater/updater.py`
- **test_unit_rerender.py** (9 connections) — `tests/updater/test_unit_rerender.py`
- **_mk_slot()** (6 connections) — `tests/updater/test_unit_rerender.py`
- **_unit_path()** (5 connections) — `tests/updater/test_unit_rerender.py`
- **test_per_slot_failure_does_not_wedge_sweep()** (5 connections) — `tests/updater/test_unit_rerender.py`
- **test_stale_unit_rewritten_and_one_daemon_reload()** (4 connections) — `tests/updater/test_unit_rerender.py`
- **test_up_to_date_unit_untouched()** (4 connections) — `tests/updater/test_unit_rerender.py`
- **test_slot_without_unit_file_skipped()** (4 connections) — `tests/updater/test_unit_rerender.py`
- **rerender_env()** (2 connections) — `tests/updater/test_unit_rerender.py`
- **Re-render every existing container slot unit through current code.      A slot's** (1 connections) — `src/hal0/updater/updater.py`
- **test_updater_module_reexports()** (1 connections) — `tests/updater/test_unit_rerender.py`
- **rerender_slot_units — the update-time slot-unit re-render sweep.  Slot units bak** (1 connections) — `tests/updater/test_unit_rerender.py`
- **Sandbox the systemd dir, container runtime, profile lookup, and     systemctl ca** (1 connections) — `tests/updater/test_unit_rerender.py`

## Relationships

- [slots_config_dir](slots_config_dir.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [updater.py](updater.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [resolve_profile_flags](resolve_profile_flags.md) (1 shared connections)

## Source Files

- `src/hal0/updater/updater.py`
- `tests/updater/test_unit_rerender.py`

## Audit Trail

- EXTRACTED: 39 (75%)
- INFERRED: 13 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*