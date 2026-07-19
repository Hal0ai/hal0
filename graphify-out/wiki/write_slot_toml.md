# write_slot_toml

> 33 nodes · cohesion 0.12

## Key Concepts

- **write_slot_toml()** (17 connections) — `src/hal0/slot_config/__init__.py`
- **slot_write_lock()** (15 connections) — `src/hal0/slot_config/__init__.py`
- **apply_preferred_runner()** (14 connections) — `src/hal0/slots/profile_adopt.py`
- **ProfileAdoptHost** (13 connections) — `src/hal0/slots/profile_adopt.py`
- **apply_preferred_profile()** (12 connections) — `src/hal0/slots/profile_adopt.py`
- **defuse_stale_mtp_on_swap()** (10 connections) — `src/hal0/slots/profile_adopt.py`
- **profile_adopt.py** (9 connections) — `src/hal0/slots/profile_adopt.py`
- **runner_fits_slot()** (7 connections) — `src/hal0/slots/profile_adopt.py`
- **profile_fits_slot()** (6 connections) — `src/hal0/slots/profile_adopt.py`
- **preferred_profile_for()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **preferred_runner_for()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **._config_file()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **._load_slot_config()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **Any** (5 connections)
- **._invalidate_cfg_cache()** (4 connections) — `src/hal0/slots/profile_adopt.py`
- **._resolve_model_info()** (4 connections) — `src/hal0/slots/profile_adopt.py`
- **TestSlotWriteLock** (4 connections) — `tests/slot_config/test_validation_and_lock.py`
- **.test_creates_one_coarse_lock_for_the_slots_dir()** (4 connections) — `tests/slot_config/test_validation_and_lock.py`
- **.test_defaults_to_configured_slots_dir()** (3 connections) — `tests/slot_config/test_validation_and_lock.py`
- **.test_reentrant_within_thread()** (3 connections) — `tests/slot_config/test_validation_and_lock.py`
- **Path** (2 connections)
- **Atomically write a slot TOML.      THE byte-level write path for ``/etc/hal0/slo** (1 connections) — `src/hal0/slot_config/__init__.py`
- **Hold the coarse cross-process lock for ALL slots/*.toml writes.      Historicall** (1 connections) — `src/hal0/slot_config/__init__.py`
- **Protocol** (1 connections)
- **Model-preferred-profile adoption + MTP defuse (P3-slots §1g).  Q1 (model profile** (1 connections) — `src/hal0/slots/profile_adopt.py`
- *... and 8 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (14 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (4 shared connections)
- [SlotConfigStore](SlotConfigStore.md) (4 shared connections)
- [get_runner](get_runner.md) (4 shared connections)
- [file_lock](file_lock.md) (3 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (2 shared connections)
- [installer.py](installer.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [migrate_slot_id_keying](migrate_slot_id_keying.md) (1 shared connections)
- [reconcile_trio_slots](reconcile_trio_slots.md) (1 shared connections)
- [test_mtp_override.py](test_mtp_override.py.md) (1 shared connections)

## Source Files

- `src/hal0/slot_config/__init__.py`
- `src/hal0/slots/profile_adopt.py`
- `tests/slot_config/test_validation_and_lock.py`

## Audit Trail

- EXTRACTED: 115 (70%)
- INFERRED: 49 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*