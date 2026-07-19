# resolve_argv

> 51 nodes · cohesion 0.06

## Key Concepts

- **resolve_argv()** (20 connections) — `src/hal0/slots/argv.py`
- **test_argv.py** (20 connections) — `tests/slots/test_argv.py`
- **normalize_argv()** (16 connections) — `src/hal0/slots/argv.py`
- **argv.py** (14 connections) — `src/hal0/slots/argv.py`
- **_split_pairs()** (8 connections) — `src/hal0/slots/argv.py`
- **_deny_managed_flags()** (6 connections) — `src/hal0/slots/argv.py`
- **_dedup()** (5 connections) — `src/hal0/slots/argv.py`
- **ResolvedArgv** (5 connections) — `src/hal0/slots/argv.py`
- **_canon()** (4 connections) — `src/hal0/slots/argv.py`
- **test_stamped_model_defaults_win_over_profile_on_flag_collision()** (4 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **test_stamped_model_still_layers_a_live_profile_segment_at_launch()** (4 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **_value_after()** (4 connections) — `tests/slots/test_argv.py`
- **FlagProvenance** (3 connections) — `src/hal0/slots/argv.py`
- **_is_flag()** (3 connections) — `src/hal0/slots/argv.py`
- **NormalizedArgv** (3 connections) — `src/hal0/slots/argv.py`
- **_Pair** (3 connections) — `src/hal0/slots/argv.py`
- **test_gp05_stamped_launch_layering.py** (3 connections) — `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- **test_agent_live_dedups_but_preserves_effective_values()** (3 connections) — `tests/slots/test_argv.py`
- **test_negative_number_is_a_value_not_a_flag()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_allows_clean_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_equivalent_argv_to_normalize()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_only_screens_untrusted_labels()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_rejects_managed_flag_in_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_rejects_multiple_managed_flags_in_one_extra_args()** (3 connections) — `tests/slots/test_argv.py`
- **test_alias_dedups_short_against_long()** (2 connections) — `tests/slots/test_argv.py`
- *... and 26 more nodes in this community*

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (6 shared connections)
- [BadRequest](BadRequest.md) (3 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [merge_flags](merge_flags.md) (2 shared connections)
- [planner.py](planner.py.md) (1 shared connections)
- [cli.py](cli.py.md) (1 shared connections)
- [models.py](models.py.md) (1 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)

## Source Files

- `src/hal0/slots/argv.py`
- `tests/golden_paths/test_gp05_stamped_launch_layering.py`
- `tests/slots/test_argv.py`

## Audit Trail

- EXTRACTED: 131 (72%)
- INFERRED: 51 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*