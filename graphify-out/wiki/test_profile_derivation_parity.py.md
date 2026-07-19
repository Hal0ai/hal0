# test_profile_derivation_parity.py

> 24 nodes · cohesion 0.09

## Key Concepts

- **test_profile_derivation_parity.py** (10 connections) — `tests/config/test_profile_derivation_parity.py`
- **profile_name_for_fit()** (7 connections) — `src/hal0/capabilities/profile_fit.py`
- **_profile_for_fit()** (6 connections) — `src/hal0/capabilities/catalog.py`
- **test_base_profile_for_backend_is_non_mtp()** (4 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_derive_profile_matrix_is_pinned()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_fit_helper_is_non_mtp_on_rocm()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_profile_for_fit_matches_shared_helper()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_profile_for_fit_twin_parity()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_reconcile_device_flip_stays_non_mtp()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **profile_fit.py** (2 connections) — `src/hal0/capabilities/profile_fit.py`
- **test_derive_profile_cpu_tts_vs_non_tts()** (2 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_derive_profile_rocm_dense_chat_coder_and_lanes()** (2 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_device_default_profiles_table_is_pinned()** (2 connections) — `tests/config/test_profile_derivation_parity.py`
- **Infer the profile implied by a picker backend.      Mirrors CapabilityOrchestrat** (1 connections) — `src/hal0/capabilities/catalog.py`
- **Shared picker/apply profile-fit inference (device → runtime profile name).  Sing** (1 connections) — `src/hal0/capabilities/profile_fit.py`
- **Infer the runtime profile name implied by a picker/apply selection.      Keeps i** (1 connections) — `src/hal0/capabilities/profile_fit.py`
- **Parity/regression lock for the device→profile derivations (finding PS-4).  The p** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **Guards the Wave-1 cpu → "cpu-llm" fix and the canonical base table.** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **catalog._profile_for_fit and orchestrator._profile_for_fit resolve to     the SA** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **The resolved catalog profile name equals the shared helper's name.** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **The picker/apply fit path NEVER forces an MTP image on ROCm — dense     chat/cod** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **_base_profile_for_backend answers the backend→non-MTP-base question so a     dra** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **Flipping a chat slot gpu-rocm → gpu-vulkan yields "vulkan", never a     ROCm MTP** (1 connections) — `tests/config/test_profile_derivation_parity.py`
- **derive_profile locks the exact current output for every cap x device.** (1 connections) — `tests/config/test_profile_derivation_parity.py`

## Relationships

- [ProfileCatalog](ProfileCatalog.md) (4 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (3 shared connections)
- [catalog.py](catalog.py.md) (2 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (2 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (1 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)

## Source Files

- `src/hal0/capabilities/catalog.py`
- `src/hal0/capabilities/profile_fit.py`
- `tests/config/test_profile_derivation_parity.py`

## Audit Trail

- EXTRACTED: 46 (75%)
- INFERRED: 15 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*