# resolve_argv

> 22 nodes

## Key Concepts

- **resolve_argv()** (22 connections) — `src/hal0/slots/argv.py`
- **test_stamped_model_still_layers_a_live_profile_segment_at_launch()** (4 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **test_stamped_model_defaults_win_over_profile_on_flag_collision()** (4 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **test_resolve_argv_screens_model_extra_args_segment()** (4 connections) — `tests/slots/test_argv.py`
- **FlagProvenance** (3 connections) — `src/hal0/slots/argv.py`
- **test_gp05_stamped_launch_layering.py** (3 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **test_resolve_argv_rejects_managed_flag_in_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_rejects_multiple_managed_flags_in_one_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_allows_clean_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_only_screens_untrusted_labels()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_does_not_screen_trusted_model_defaults_ngl()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_attributes_winning_source()** (2 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_omits_append_flags_from_provenance()** (2 connections) — `tests/slots/test_argv.py`
- **One surviving flag and the input segment it was resolved from.** (1 connections) — `src/hal0/slots/argv.py`
- **Resolve ordered ``(source_label, tokens)`` segments into one deduped argv.** (1 connections) — `src/hal0/slots/argv.py`
- **Golden path #5 (pull→assign→infer) — launch-time profile-read invariant.  spec-f** (1 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **CURRENT behaviour (increment 1): even when the model carries a stamped     ``def** (1 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **When the stamp and the live profile set the SAME flag, the model tune     wins (** (1 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **A slot's real extra_args (bench tuning, no managed flags) passes through.** (1 connections) — `tests/slots/test_argv.py`
- **A managed flag in a non-``extra_args`` (trusted) segment is not screened.      `** (1 connections) — `tests/slots/test_argv.py`
- **A model's free-form ``defaults.extra_args`` (the ``model_extra_args``     segmen** (1 connections) — `tests/slots/test_argv.py`
- **The ``-ngl`` hal0 computes from the schema field ``defaults.n_gpu_layers``     r** (1 connections) — `tests/slots/test_argv.py`

## Relationships

- [test_argv.py](test_argv.py.md) (9 shared connections)
- [argv.py](argv.py.md) (6 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (5 shared connections)
- [BadRequest](BadRequest.md) (3 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (1 shared connections)

## Source Files

- `src/hal0/slots/argv.py`
- `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- `tests/slots/test_argv.py`

## Audit Trail

- EXTRACTED: 38 (56%)
- INFERRED: 30 (44%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*