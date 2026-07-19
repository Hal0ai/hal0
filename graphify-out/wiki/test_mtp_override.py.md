# test_mtp_override.py

> 40 nodes

## Key Concepts

- **test_mtp_override.py** (27 connections) — `tests/config/test_mtp_override.py`
- **_effective_mtp()** (14 connections) — `src/hal0/providers/container.py`
- **model_is_mtp_eligible()** (13 connections) — `src/hal0/model_meta/__init__.py`
- **_mtp_model()** (6 connections) — `tests/config/test_mtp_override.py`
- **build_mtp_flag_bundle()** (5 connections) — `src/hal0/config/schema.py`
- **_profile()** (5 connections) — `tests/config/test_mtp_override.py`
- **_plain_model()** (5 connections) — `tests/config/test_mtp_override.py`
- **test_override_none_never_expands_even_when_profile_mtp_true()** (4 connections) — `tests/config/test_mtp_override.py`
- **test_auto_off_when_runner_does_not_support_mtp()** (4 connections) — `tests/config/test_mtp_override.py`
- **test_defaults_mtp_true_beats_absent_tag_and_is_unconditional()** (4 connections) — `tests/config/test_mtp_override.py`
- **test_auto_off_breadcrumb_only_on_launch_path()** (4 connections) — `tests/config/test_mtp_override.py`
- **test_override_true_appends_bundle_regardless_of_profile_mtp()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_override_false_drops_bundle_regardless_of_profile_mtp()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_profile_image_and_flags_honors_override()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_no_filename_marker_sniffing_anymore()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_explicit_defaults_mtp_false_wins_over_present_tag()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_auto_on_when_tag_eligible_and_runner_supports_mtp()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_auto_off_when_model_not_eligible()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_slot_override_true_forces_on_even_for_plain_model_or_unsupported_runner()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_slot_override_false_forces_off_even_for_eligible()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_defaults_mtp_false_beats_present_tag()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_defaults_mtp_none_falls_back_to_tag_and_runner_gate()** (3 connections) — `tests/config/test_mtp_override.py`
- **test_bundle_draft_device_tracks_backend()** (2 connections) — `tests/config/test_mtp_override.py`
- **test_bundle_unknown_backend_defaults_rocm0()** (2 connections) — `tests/config/test_mtp_override.py`
- **test_eligible_by_registry_tag()** (2 connections) — `tests/config/test_mtp_override.py`
- *... and 15 more nodes in this community*

## Relationships

- [resolve_profile_flags](resolve_profile_flags.md) (5 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (3 shared connections)
- [ProfileConfig](ProfileConfig.md) (2 shared connections)
- [schema.py](schema.py.md) (1 shared connections)
- [write_slot_toml](write_slot_toml.md) (1 shared connections)
- [test_mtp_defuse.py](test_mtp_defuse.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/model_meta/__init__.py`
- `src/hal0/providers/container.py`
- `tests/config/test_mtp_override.py`

## Audit Trail

- EXTRACTED: 106 (71%)
- INFERRED: 43 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*