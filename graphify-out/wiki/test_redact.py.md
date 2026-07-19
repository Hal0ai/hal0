# test_redact.py

> 54 nodes

## Key Concepts

- **test_redact.py** (21 connections) — `tests/api/test_redact.py`
- **redact_config()** (20 connections) — `src/hal0/api/_redact.py`
- **is_sensitive_key()** (8 connections) — `src/hal0/api/_redact.py`
- **redact_value()** (8 connections) — `src/hal0/api/_redact.py`
- **_redact.py** (4 connections) — `src/hal0/api/_redact.py`
- **isolated_client()** (4 connections) — `tests/api/test_redact.py`
- **TestBareKeySuffix** (4 connections) — `tests/api/test_redact.py`
- **test_is_sensitive_key_matches_documented_patterns()** (3 connections) — `tests/api/test_redact.py`
- **test_is_sensitive_key_leaves_plain_keys_alone()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_value_masks_nonempty_token()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_value_empty_string_yields_set_false()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_value_none_yields_set_false()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_value_zero_is_treated_as_set()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_token_key_masked_with_set_true()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_plain_key_passes_through_unmasked()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_empty_sensitive_key_yields_set_false()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_does_not_mutate_input()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_walks_nested_dicts()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_walks_lists_of_dicts()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_sensitive_container_masks_wholesale()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_list_of_scalars_passes_through()** (3 connections) — `tests/api/test_redact.py`
- **test_redact_config_scalars_returned_verbatim()** (3 connections) — `tests/api/test_redact.py`
- **TestClient** (3 connections)
- **test_settings_get_redacts_sensitive_keys()** (3 connections) — `tests/api/test_redact.py`
- **test_settings_get_empty_sensitive_key_yields_set_false()** (3 connections) — `tests/api/test_redact.py`
- *... and 29 more nodes in this community*

## Relationships

- [_write_diagnostics_section](_write_diagnostics_section.md) (2 shared connections)
- [config.py](config.py.md) (2 shared connections)
- [providers.py](providers.py.md) (1 shared connections)
- [settings.py](settings.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/api/_redact.py`
- `tests/api/test_redact.py`

## Audit Trail

- EXTRACTED: 114 (73%)
- INFERRED: 43 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*