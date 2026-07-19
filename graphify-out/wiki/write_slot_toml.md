# write_slot_toml

> 30 nodes

## Key Concepts

- **write_slot_toml()** (17 connections) — `src/hal0/slot_config/__init__.py`
- **slot_write_lock()** (15 connections) — `src/hal0/slot_config/__init__.py`
- **apply_preferred_runner()** (14 connections) — `src/hal0/slots/profile_adopt.py`
- **ProfileAdoptHost** (13 connections) — `src/hal0/slots/profile_adopt.py`
- **apply_preferred_profile()** (12 connections) — `src/hal0/slots/profile_adopt.py`
- **defuse_stale_mtp_on_swap()** (10 connections) — `src/hal0/slots/profile_adopt.py`
- **device_to_backend()** (9 connections) — `src/hal0/model_meta/__init__.py`
- **profile_adopt.py** (9 connections) — `src/hal0/slots/profile_adopt.py`
- **runner_fits_slot()** (7 connections) — `src/hal0/slots/profile_adopt.py`
- **profile_fits_slot()** (6 connections) — `src/hal0/slots/profile_adopt.py`
- **Any** (5 connections)
- **._load_slot_config()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **._config_file()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **preferred_profile_for()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **preferred_runner_for()** (5 connections) — `src/hal0/slots/profile_adopt.py`
- **._resolve_model_info()** (4 connections) — `src/hal0/slots/profile_adopt.py`
- **._invalidate_cfg_cache()** (4 connections) — `src/hal0/slots/profile_adopt.py`
- **test_device_to_backend()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **Map hal0's ``device`` enum onto the recipe+backend pair.      Args:         devi** (1 connections) — `src/hal0/model_meta/__init__.py`
- **Atomically write a slot TOML.      THE byte-level write path for ``/etc/hal0/slo** (1 connections) — `src/hal0/slot_config/__init__.py`
- **Hold the coarse cross-process lock for ALL slots/*.toml writes.      Historicall** (1 connections) — `src/hal0/slot_config/__init__.py`
- **Model-preferred-profile adoption + MTP defuse (P3-slots §1g).  Q1 (model profile** (1 connections) — `src/hal0/slots/profile_adopt.py`
- **Narrow seam this module needs from ``SlotManager``.** (1 connections) — `src/hal0/slots/profile_adopt.py`
- **The model's preferred runtime profile name (``defaults.profile``).      A regist** (1 connections) — `src/hal0/slots/profile_adopt.py`
- **True when ``profile_name`` is safe to adopt for this slot.      A model's profil** (1 connections) — `src/hal0/slots/profile_adopt.py`
- *... and 5 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (12 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (6 shared connections)
- [file_lock](file_lock.md) (4 shared connections)
- [get_runner](get_runner.md) (4 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (3 shared connections)
- [compute_config_drift](compute_config_drift.md) (2 shared connections)
- [config_enrichment](config_enrichment.md) (1 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)
- [installer.py](installer.py.md) (1 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/model_meta/__init__.py`
- `src/hal0/slot_config/__init__.py`
- `src/hal0/slots/profile_adopt.py`
- `tests/model_meta/test_model_meta.py`

## Audit Trail

- EXTRACTED: 107 (67%)
- INFERRED: 52 (33%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*