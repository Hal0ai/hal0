# merge_flags

> 47 nodes · cohesion 0.06

## Key Concepts

- **merge_flags()** (24 connections) — `src/hal0/slots/argv.py`
- **test_flag_merge.py** (21 connections) — `tests/slots/test_flag_merge.py`
- **events.py** (5 connections) — `src/hal0/realtime/events.py`
- **event()** (5 connections) — `src/hal0/realtime/events.py`
- **error_event()** (4 connections) — `src/hal0/realtime/events.py`
- **new_id()** (4 connections) — `src/hal0/realtime/events.py`
- **test_unbalanced_quote_falls_back_to_dumb_concat()** (4 connections) — `tests/slots/test_flag_merge.py`
- **test_unbalanced_quote_on_one_side_only()** (4 connections) — `tests/slots/test_flag_merge.py`
- **test_boolean_flag_without_value()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_lora_is_appended_not_deduped()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_quoted_value_survives_round_trip()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_dedups_against_long_alias()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_dedups_last_wins()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_short_flag_negative_value_is_not_a_flag()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_can_remove_value_by_overriding_with_bare_flag()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_flag_without_value_strips_model_flag_with_value()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_keeps_order_after_model_remainder()** (3 connections) — `tests/slots/test_flag_merge.py`
- **test_slot_overrides_model_threads()** (3 connections) — `tests/slots/test_flag_merge.py`
- **Any** (2 connections)
- **unix_ms()** (2 connections) — `src/hal0/realtime/events.py`
- **LogCaptureFixture** (2 connections)
- **test_draft_model_is_appended_not_deduped()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_empty_strings_return_empty()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_mixed_short_and_long_flags_merge()** (2 connections) — `tests/slots/test_flag_merge.py`
- **test_none_and_none_returns_empty()** (2 connections) — `tests/slots/test_flag_merge.py`
- *... and 22 more nodes in this community*

## Relationships

- [resolve_argv](resolve_argv.md) (2 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/realtime/events.py`
- `src/hal0/slots/argv.py`
- `tests/slots/test_flag_merge.py`

## Audit Trail

- EXTRACTED: 98 (70%)
- INFERRED: 43 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*