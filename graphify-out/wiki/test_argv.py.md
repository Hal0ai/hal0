# test_argv.py

> 17 nodes

## Key Concepts

- **test_argv.py** (22 connections) — `tests/slots/test_argv.py`
- **normalize_argv()** (16 connections) — `src/hal0/slots/argv.py`
- **_value_after()** (4 connections) — `tests/slots/test_argv.py`
- **test_agent_live_dedups_but_preserves_effective_values()** (3 connections) — `tests/slots/test_argv.py`
- **test_negative_number_is_a_value_not_a_flag()** (3 connections) — `tests/slots/test_argv.py`
- **test_resolve_argv_equivalent_argv_to_normalize()** (3 connections) — `tests/slots/test_argv.py`
- **test_alias_dedups_short_against_long()** (2 connections) — `tests/slots/test_argv.py`
- **test_normalize_is_idempotent()** (2 connections) — `tests/slots/test_argv.py`
- **test_last_value_wins_on_conflict()** (2 connections) — `tests/slots/test_argv.py`
- **test_bool_flags_collapse_to_one()** (2 connections) — `tests/slots/test_argv.py`
- **test_append_flags_are_never_deduped()** (2 connections) — `tests/slots/test_argv.py`
- **test_bare_positionals_preserved()** (2 connections) — `tests/slots/test_argv.py`
- **test_empty_is_noop()** (2 connections) — `tests/slots/test_argv.py`
- **Dedup ``tokens`` keeping the last occurrence of each scalar/bool flag.      Effe** (1 connections) — `src/hal0/slots/argv.py`
- **test_managed_args_denylist_covers_expected_flags()** (1 connections) — `tests/slots/test_argv.py`
- **Unit + golden-parity tests for hal0.slots.argv.normalize_argv.  The golden fixtu** (1 connections) — `tests/slots/test_argv.py`
- **Last value following ``flag`` in ``tokens`` (the effective value).** (1 connections) — `tests/slots/test_argv.py`

## Relationships

- [resolve_argv](resolve_argv.md) (9 shared connections)
- [argv.py](argv.py.md) (4 shared connections)
- [merge_flags](merge_flags.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/argv.py`
- `tests/slots/test_argv.py`

## Audit Trail

- EXTRACTED: 48 (70%)
- INFERRED: 21 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*