# TestFamilyDefaults

> 18 nodes

## Key Concepts

- **TestFamilyDefaults** (10 connections) — `tests/providers/test_container.py`
- **family_flags()** (6 connections) — `src/hal0/config/schema.py`
- **model_family()** (5 connections) — `src/hal0/config/schema.py`
- **test_family_flags_prefers_architecture_over_filename_scan()** (4 connections) — `tests/providers/test_capability_injection.py`
- **.test_gemma_on_q8_profile_pins_f16_kv()** (4 connections) — `tests/providers/test_container.py`
- **.test_non_gemma_on_q8_profile_keeps_q8()** (4 connections) — `tests/providers/test_container.py`
- **.test_slot_extra_args_still_beats_family()** (4 connections) — `tests/providers/test_container.py`
- **.test_vulkan_seed_is_basic_no_forced_kv_quant()** (4 connections) — `tests/providers/test_container.py`
- **.test_model_family_token_scan()** (2 connections) — `tests/providers/test_container.py`
- **.test_family_flags_lookup()** (2 connections) — `tests/providers/test_container.py`
- **Best-effort model family, preferring the registry's ``architecture``.      §7.1a** (1 connections) — `src/hal0/config/schema.py`
- **The :data:`FAMILY_DEFAULTS` flag string for the model's family, else ''.** (1 connections) — `src/hal0/config/schema.py`
- **§7.1a / ML-5: family_flags/model_family re-keyed off Model.architecture     firs** (1 connections) — `tests/providers/test_capability_injection.py`
- **FAMILY_DEFAULTS — the per-family override layer (gemma → f16 KV).** (1 connections) — `tests/providers/test_container.py`
- **A gemma model on a q8 profile resolves to f16 KV — profile's         -ctk q8_0 i** (1 connections) — `tests/providers/test_container.py`
- **A qwen model on the same profile is untouched — no family entry.** (1 connections) — `tests/providers/test_container.py`
- **A hand-authored [server].extra_args overrides the family default.** (1 connections) — `tests/providers/test_container.py`
- **The vulkan seed ships minimal flags with NO forced KV quant.          The 2026-0** (1 connections) — `tests/providers/test_container.py`

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (6 shared connections)
- [resolve_profile_flags](resolve_profile_flags.md) (4 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [Mount](Mount.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/providers/test_capability_injection.py`
- `tests/providers/test_container.py`

## Audit Trail

- EXTRACTED: 38 (72%)
- INFERRED: 15 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*