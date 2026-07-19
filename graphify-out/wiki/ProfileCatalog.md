# ProfileCatalog

> 47 nodes · cohesion 0.07

## Key Concepts

- **ProfileCatalog** (56 connections) — `src/hal0/profiles/__init__.py`
- **test_catalog.py** (12 connections) — `tests/profiles/test_catalog.py`
- **ResolvedProfile** (11 connections) — `src/hal0/profiles/__init__.py`
- **._resolve_item()** (10 connections) — `src/hal0/profiles/__init__.py`
- **.update()** (9 connections) — `src/hal0/profiles/__init__.py`
- **update_profile()** (8 connections) — `src/hal0/api/routes/profiles.py`
- **.create()** (8 connections) — `src/hal0/profiles/__init__.py`
- **._profile_for_fit()** (7 connections) — `src/hal0/capabilities/orchestrator.py`
- **__init__.py** (7 connections) — `src/hal0/profiles/__init__.py`
- **.delete()** (7 connections) — `src/hal0/profiles/__init__.py`
- **ProfilePatch** (6 connections) — `src/hal0/profiles/__init__.py`
- **.list()** (5 connections) — `src/hal0/profiles/__init__.py`
- **.resolve()** (5 connections) — `src/hal0/profiles/__init__.py`
- **._slot_profiles()** (5 connections) — `src/hal0/profiles/__init__.py`
- **_runtime_family()** (5 connections) — `src/hal0/profiles/__init__.py`
- **._guard_custom()** (4 connections) — `src/hal0/profiles/__init__.py`
- **.slots_using()** (4 connections) — `src/hal0/profiles/__init__.py`
- **._used_by_index()** (4 connections) — `src/hal0/profiles/__init__.py`
- **_supported_slot_types()** (4 connections) — `src/hal0/profiles/__init__.py`
- **test_cloned_from_defaults_to_none_and_survives_update()** (4 connections) — `tests/profiles/test_catalog.py`
- **test_create_update_delete_profile()** (4 connections) — `tests/profiles/test_catalog.py`
- **test_delete_profile_in_use_raises_conflict()** (4 connections) — `tests/profiles/test_catalog.py`
- **._validate_name()** (3 connections) — `src/hal0/profiles/__init__.py`
- **test_cloned_from_persists_and_round_trips()** (3 connections) — `tests/profiles/test_catalog.py`
- **test_custom_profile_has_no_bench_and_round_trips_intent_quant()** (3 connections) — `tests/profiles/test_catalog.py`
- *... and 22 more nodes in this community*

## Relationships

- [profiles.py](profiles.py.md) (10 shared connections)
- [ProfileConfig](ProfileConfig.md) (10 shared connections)
- [BoardStore](BoardStore.md) (8 shared connections)
- [load_profiles_config](load_profiles_config.md) (6 shared connections)
- [evaluate_model_fit](evaluate_model_fit.md) (6 shared connections)
- [test_profile_derivation_parity.py](test_profile_derivation_parity.py.md) (4 shared connections)
- [save_profiles_config](save_profiles_config.md) (3 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (2 shared connections)
- [record_action](record_action.md) (1 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (1 shared connections)
- [.apply](apply.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/profiles.py`
- `src/hal0/capabilities/orchestrator.py`
- `src/hal0/profiles/__init__.py`
- `tests/profiles/test_catalog.py`

## Audit Trail

- EXTRACTED: 146 (63%)
- INFERRED: 84 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*