# heal_missing_llm_type

> 14 nodes · cohesion 0.20

## Key Concepts

- **heal_missing_llm_type()** (11 connections) — `src/hal0/slots/_cfg_helpers.py`
- **TestHealHelper** (7 connections) — `tests/slots/test_type_heal.py`
- **test_type_heal.py** (3 connections) — `tests/slots/test_type_heal.py`
- **TestHealOnLoad** (3 connections) — `tests/slots/test_type_heal.py`
- **.test_empty_string_type_is_healed()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_explicit_type_is_respected()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_image_table_is_not_healed()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_no_model_table_is_not_healed()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_non_llama_provider_is_not_healed()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_type_less_llm_shaped_slot_heals()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_loader_heals_type_less_flat_slot()** (2 connections) — `tests/slots/test_type_heal.py`
- **.test_manager_iter_configs_heals()** (2 connections) — `tests/slots/test_type_heal.py`
- **Default a type-less, llm-shaped slot config to ``type="llm"`` in place.      A c** (1 connections) — `src/hal0/slots/_cfg_helpers.py`
- **Heal-on-load: a type-less, llm-shaped slot defaults to ``type="llm"`` (O23).  ``** (1 connections) — `tests/slots/test_type_heal.py`

## Relationships

- [SlotConfigError](SlotConfigError.md) (3 shared connections)
- [load_manifest](load_manifest.md) (1 shared connections)
- [load_slot_config](load_slot_config.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/slots/_cfg_helpers.py`
- `tests/slots/test_type_heal.py`

## Audit Trail

- EXTRACTED: 26 (62%)
- INFERRED: 16 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*