# merge_flags

> 36 nodes

## Key Concepts

- **merge_flags()** (24 connections) — `src/hal0/slots/argv.py`
- **test_flag_merge.py** (21 connections) — `tests/slots/test_flag_merge.py`
- **test_unbalanced_quote_falls_back_to_dumb_concat()** (4 connections) — `tests/slots/test_flag_merge.py`
- **test_unbalanced_quote_on_one_side_only()** (4 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_overrides_model_threads()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_flag_without_value_strips_model_flag_with_value()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_keeps_order_after_model_remainder()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_lora_is_appended_not_deduped()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_quoted_value_survives_round_trip()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_boolean_flag_without_value()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_can_remove_value_by_overriding_with_bare_flag()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_dedups_last_wins()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_dedups_against_long_alias()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_negative_value_is_not_a_flag()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_none_and_none_returns_empty()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_empty_strings_return_empty()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_none_model_only_slot()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_only_model_none_slot()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_one_sided_input_is_trimmed()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_draft_model_is_appended_not_deduped()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_override_kv_is_appended_not_deduped()** (2 connections) — `tests/slots/test_flag_merge.py`
- **LogCaptureFixture** (2 connections)
- **test_mixed_short_and_long_flags_merge()** (2 connections) — `tests/slots/test_flag_merge.py`
- **Combine model-default and slot-override CLI flag strings (last-wins).      Args:** (1 connections) — `src/hal0/slots/argv.py`
- **Unit tests for hal0.slots.argv.merge_flags.  (The ``hal0.slots.flag_merge`` modu** (1 connections) — `tests/slots/test_flag_merge.py`
- *... and 11 more nodes in this community*

## Relationships

- [resolve_profile_flags](resolve_profile_flags.md) (1 shared connections)
- [argv.py](argv.py.md) (1 shared connections)
- [test_argv.py](test_argv.py.md) (1 shared connections)
- [events.py](events.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/argv.py`
- `tests/slots/test_flag_merge.py`

## Audit Trail

- EXTRACTED: 72 (63%)
- INFERRED: 42 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*