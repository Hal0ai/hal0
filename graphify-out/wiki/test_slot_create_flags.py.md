# test_slot_create_flags.py

> 26 nodes · cohesion 0.11

## Key Concepts

- **test_slot_create_flags.py** (12 connections) — `tests/cli/test_slot_create_flags.py`
- **Any** (6 connections)
- **MonkeyPatch** (5 connections)
- **captured_post()** (4 connections) — `tests/cli/test_slot_create_flags.py`
- **Path** (4 connections)
- **test_bare_create_on_strix_halo_resolves_to_vulkan()** (4 connections) — `tests/cli/test_slot_create_flags.py`
- **test_default_hardware_reads_probe_json()** (4 connections) — `tests/cli/test_slot_create_flags.py`
- **test_default_hardware_vulkan_fallback()** (4 connections) — `tests/cli/test_slot_create_flags.py`
- **test_default_hardware_cpu_when_no_gpu()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_hardware_flag_overrides_default()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_invalid_hardware_value_rejected_by_typer()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_legacy_backend_flag_translates_to_provider()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_legacy_backend_with_invalid_value_errors()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_provider_flag_sets_provider_and_default_hardware()** (3 connections) — `tests/cli/test_slot_create_flags.py`
- **test_help_lists_new_flags()** (2 connections) — `tests/cli/test_slot_create_flags.py`
- **Tests for ``hal0 slot create`` flag semantics.  Regression coverage for the hal0** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **Deprecated ``--backend flm`` is translated to provider=flm + warns on stderr.** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **``--backend vulkan`` (hardware-shaped) is rejected as not-a-provider.** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **``_detect_default_hardware`` picks rocm for AMD+compute_capable GPUs.** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **Missing probe → vulkan (safe default that works on most GPUs).** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **``--hardware foo`` is rejected at the Typer parsing layer.      Because ``--hard** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **A bare ``slot create primary`` on a Strix Halo fixture auto-resolves     ``--har** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **Stub the API surface ``slot_create`` touches and capture the POST body.** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **``slot create --help`` mentions --provider and --hardware.      Click colors the** (1 connections) — `tests/cli/test_slot_create_flags.py`
- **``--provider llama-server`` (no --hardware) uses the auto-detected default.** (1 connections) — `tests/cli/test_slot_create_flags.py`
- *... and 1 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/cli/test_slot_create_flags.py`

## Audit Trail

- EXTRACTED: 74 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*