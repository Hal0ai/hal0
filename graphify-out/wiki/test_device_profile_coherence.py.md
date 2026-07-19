# test_device_profile_coherence.py

> 15 nodes · cohesion 0.18

## Key Concepts

- **test_device_profile_coherence.py** (8 connections) — `tests/slots/test_device_profile_coherence.py`
- **_gpu_cfg()** (6 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_create_rejects_incoherent_pair()** (5 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_explicit_contradiction_rejected()** (5 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_device_change_reconciles_conflicting_profile()** (4 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_profile_change_drives_device()** (4 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_unrelated_update_preserves_coherent_pair()** (4 connections) — `tests/slots/test_device_profile_coherence.py`
- **test_non_gpu_profile_untouched()** (3 connections) — `tests/slots/test_device_profile_coherence.py`
- **Device↔profile backend coherence on slot create / update_config.  A GPU slot car** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **A non-GPU profile (backend=None) never triggers device reconciliation.** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **Switching the profile re-derives device — the exact utility-slot bug.      A vul** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **Flipping device across backends drops an incompatible profile.      A cross-back** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **A change that touches neither device nor profile leaves both intact.** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **Changing both fields to conflicting backends is an operator error.** (1 connections) — `tests/slots/test_device_profile_coherence.py`
- **create() must refuse a vulkan device paired with a rocm profile.      This is th** (1 connections) — `tests/slots/test_device_profile_coherence.py`

## Relationships

- [SlotManager](SlotManager.md) (6 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)

## Source Files

- `tests/slots/test_device_profile_coherence.py`

## Audit Trail

- EXTRACTED: 38 (83%)
- INFERRED: 8 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*