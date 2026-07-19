# test_notes.py

> 42 nodes · cohesion 0.08

## Key Concepts

- **test_notes.py** (20 connections) — `tests/release/test_notes.py`
- **extract_changelog_section()** (19 connections) — `src/hal0/release/notes.py`
- **extract_structured()** (7 connections) — `src/hal0/release/notes.py`
- **_git_changelog()** (5 connections) — `scripts/gen_release_notes.py`
- **gen_release_notes.py** (4 connections) — `scripts/gen_release_notes.py`
- **_git()** (4 connections) — `scripts/gen_release_notes.py`
- **main()** (4 connections) — `scripts/gen_release_notes.py`
- **_prev_tag()** (4 connections) — `scripts/gen_release_notes.py`
- **test_extract_structured_missing_subsections_are_empty()** (4 connections) — `tests/release/test_notes.py`
- **notes.py** (3 connections) — `src/hal0/release/notes.py`
- **test_does_not_match_version_that_is_prefix_of_another()** (3 connections) — `tests/release/test_notes.py`
- **test_extract_structured_pulls_all_three_lists()** (3 connections) — `tests/release/test_notes.py`
- **test_extracts_last_section_no_trailing_header()** (3 connections) — `tests/release/test_notes.py`
- **test_header_line_excluded()** (3 connections) — `tests/release/test_notes.py`
- **test_last_section_in_single_entry_document()** (3 connections) — `tests/release/test_notes.py`
- **test_longer_version_not_matched_by_shorter_query()** (3 connections) — `tests/release/test_notes.py`
- **test_result_is_stripped()** (3 connections) — `tests/release/test_notes.py`
- **test_accepts_version_with_leading_v()** (2 connections) — `tests/release/test_notes.py`
- **test_accepts_version_without_leading_v()** (2 connections) — `tests/release/test_notes.py`
- **test_empty_changelog_returns_empty_string()** (2 connections) — `tests/release/test_notes.py`
- **test_empty_version_returns_empty_string()** (2 connections) — `tests/release/test_notes.py`
- **test_extract_structured_case_insensitive_and_skips_nested_bullets()** (2 connections) — `tests/release/test_notes.py`
- **test_extract_structured_empty_input()** (2 connections) — `tests/release/test_notes.py`
- **test_extracts_first_section()** (2 connections) — `tests/release/test_notes.py`
- **test_extracts_middle_section()** (2 connections) — `tests/release/test_notes.py`
- *... and 17 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `scripts/gen_release_notes.py`
- `src/hal0/release/notes.py`
- `tests/release/test_notes.py`

## Audit Trail

- EXTRACTED: 86 (66%)
- INFERRED: 44 (34%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*