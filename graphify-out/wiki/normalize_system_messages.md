# normalize_system_messages

> 17 nodes

## Key Concepts

- **normalize_system_messages()** (14 connections) — `src/hal0/normalize/messages.py`
- **test_messages.py** (11 connections) — `tests/normalize/test_messages.py`
- **messages.py** (2 connections) — `src/hal0/normalize/messages.py`
- **test_non_list_passthrough()** (2 connections) — `tests/normalize/test_messages.py`
- **test_empty_list_passthrough()** (2 connections) — `tests/normalize/test_messages.py`
- **test_no_system_messages_no_allocation()** (2 connections) — `tests/normalize/test_messages.py`
- **test_system_after_user_is_hoisted()** (2 connections) — `tests/normalize/test_messages.py`
- **test_already_canonical_no_copy()** (2 connections) — `tests/normalize/test_messages.py`
- **test_multi_system_collapses_into_single()** (2 connections) — `tests/normalize/test_messages.py`
- **test_three_or_more_systems_still_collapse()** (2 connections) — `tests/normalize/test_messages.py`
- **test_junk_entries_preserved_as_others()** (2 connections) — `tests/normalize/test_messages.py`
- **test_system_entry_without_content_key_still_hoists()** (2 connections) — `tests/normalize/test_messages.py`
- **test_user_only_passthrough_with_extras()** (2 connections) — `tests/normalize/test_messages.py`
- **Any** (1 connections)
- **Per-request normalisation helpers that operate on the OpenAI ``messages`` array.** (1 connections) — `src/hal0/normalize/messages.py`
- **Collapse every ``role='system'`` entry into one and hoist it to position 0.** (1 connections) — `src/hal0/normalize/messages.py`
- **Tests for :func:`hal0.normalize.messages.normalize_system_messages`.  Covers the** (1 connections) — `tests/normalize/test_messages.py`

## Relationships

- [v1.py](v1.py.md) (1 shared connections)

## Source Files

- `src/hal0/normalize/messages.py`
- `tests/normalize/test_messages.py`

## Audit Trail

- EXTRACTED: 30 (59%)
- INFERRED: 21 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*