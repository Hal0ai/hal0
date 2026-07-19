# apply_thinking_policy

> 23 nodes · cohesion 0.15

## Key Concepts

- **apply_thinking_policy()** (21 connections) — `src/hal0/normalize/thinking.py`
- **test_thinking.py** (14 connections) — `tests/normalize/test_thinking.py`
- **thinking.py** (4 connections) — `src/hal0/normalize/thinking.py`
- **_caller_intent()** (4 connections) — `src/hal0/normalize/thinking.py`
- **_explicit_kwarg_set()** (3 connections) — `src/hal0/normalize/thinking.py`
- **Any** (3 connections)
- **test_does_not_mutate_input()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_idempotent()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_idempotent_after_top_level_translation()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_injects_chat_template_kwargs_enable_thinking_false_by_default()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_no_think_marker_passthrough_untouched()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_opt_out_chat_template_kwargs_preserved()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_opt_out_thinking_dict_field_preserved()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_per_slot_default_false_is_baseline()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_per_slot_default_overridden_by_caller()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_per_slot_default_thinking_true()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_preserves_sibling_chat_template_kwargs()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_top_level_enable_thinking_false_translated_to_kwarg()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_top_level_enable_thinking_true_translated_to_kwarg()** (2 connections) — `tests/normalize/test_thinking.py`
- **test_top_level_thinking_bool_translated()** (2 connections) — `tests/normalize/test_thinking.py`
- **Reasoning-suppression policy for dispatcher-bound chat requests.  We steer reaso** (1 connections) — `src/hal0/normalize/thinking.py`
- **The caller's top-level boolean thinking intent, or None if unset.** (1 connections) — `src/hal0/normalize/thinking.py`
- **Return a copy of ``body`` whose reasoning is steered via     ``chat_template_kwa** (1 connections) — `src/hal0/normalize/thinking.py`

## Relationships

- [v1.py](v1.py.md) (1 shared connections)
- [_run_nonstreaming_turn](_run_nonstreaming_turn.md) (1 shared connections)

## Source Files

- `src/hal0/normalize/thinking.py`
- `tests/normalize/test_thinking.py`

## Audit Trail

- EXTRACTED: 50 (62%)
- INFERRED: 30 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*