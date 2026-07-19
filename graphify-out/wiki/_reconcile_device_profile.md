# _reconcile_device_profile

> 20 nodes

## Key Concepts

- **_reconcile_device_profile()** (11 connections) — `src/hal0/slots/config_write.py`
- **config_write.py** (10 connections) — `src/hal0/slots/config_write.py`
- **Any** (9 connections)
- **check_npu_exclusivity()** (8 connections) — `src/hal0/slots/config_write.py`
- **check_default_uniqueness()** (8 connections) — `src/hal0/slots/config_write.py`
- **reconcile_and_guard_slot_config()** (8 connections) — `src/hal0/slots/config_write.py`
- **_iter_peer_configs()** (7 connections) — `src/hal0/slots/config_write.py`
- **reconcile_slot_updates()** (7 connections) — `src/hal0/slots/config_write.py`
- **_base_profile_for_backend()** (5 connections) — `src/hal0/slots/config_write.py`
- **_read_slot_toml_dict()** (5 connections) — `src/hal0/slots/config_write.py`
- **Path** (5 connections)
- **Shared slot-config write pipeline (guards for every writer).  ``SlotManager.upda** (1 connections) — `src/hal0/slots/config_write.py`
- **Pick the canonical (non-MTP) seed profile name for a GPU backend.      Prefers t** (1 connections) — `src/hal0/slots/config_write.py`
- **Keep a GPU slot's ``device`` and ``profile.backend`` coherent in place.      A G** (1 connections) — `src/hal0/slots/config_write.py`
- **Best-effort raw read of one ``slots/*.toml`` (with the [slot] hoist).      Retur** (1 connections) — `src/hal0/slots/config_write.py`
- **(name, cfg) for every readable configured slot other than ``slot_name``.** (1 connections) — `src/hal0/slots/config_write.py`
- **Reject a write that would land a second enabled NPU LLM anchor.      Sync core o** (1 connections) — `src/hal0/slots/config_write.py`
- **Reject a write that would land a second ``default=true`` per type.      Sync cor** (1 connections) — `src/hal0/slots/config_write.py`
- **Normalize + merge ``updates`` onto ``base`` and keep device/profile coherent.** (1 connections) — `src/hal0/slots/config_write.py`
- **The full guarded write pipeline: normalize + merge + reconcile + guards.      Ra** (1 connections) — `src/hal0/slots/config_write.py`

## Relationships

- [SlotState](SlotState.md) (6 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (2 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (2 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)
- [map_backend_to_device](map_backend_to_device.md) (1 shared connections)
- [write_slot_toml](write_slot_toml.md) (1 shared connections)
- [merge_slot_config](merge_slot_config.md) (1 shared connections)

## Source Files

- `src/hal0/slots/config_write.py`

## Audit Trail

- EXTRACTED: 78 (85%)
- INFERRED: 14 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*