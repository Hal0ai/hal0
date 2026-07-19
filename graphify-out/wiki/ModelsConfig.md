# ModelsConfig

> 30 nodes

## Key Concepts

- **ModelsConfig** (30 connections) — `src/hal0/config/schema.py`
- **TestScanRoots** (7 connections) — `tests/config/test_models_config.py`
- **TestRootsValidator** (5 connections) — `tests/config/test_models_config.py`
- **test_models_config.py** (4 connections) — `tests/config/test_models_config.py`
- **TestModelsConfigDefaults** (3 connections) — `tests/config/test_models_config.py`
- **.test_attached_to_hal0_config()** (3 connections) — `tests/config/test_models_config.py`
- **.flm_store_is_absolute_when_set()** (2 connections) — `src/hal0/config/schema.py`
- **.scan_roots()** (2 connections) — `src/hal0/config/schema.py`
- **.roots_are_absolute()** (2 connections) — `src/hal0/config/schema.py`
- **.store_is_absolute_when_set()** (2 connections) — `src/hal0/config/schema.py`
- **.effective_store()** (2 connections) — `src/hal0/config/schema.py`
- **.test_defaults()** (2 connections) — `tests/config/test_models_config.py`
- **.test_absolute_path_accepted()** (2 connections) — `tests/config/test_models_config.py`
- **.test_relative_path_rejected()** (2 connections) — `tests/config/test_models_config.py`
- **.test_empty_string_rejected()** (2 connections) — `tests/config/test_models_config.py`
- **.test_dot_relative_rejected()** (2 connections) — `tests/config/test_models_config.py`
- **.test_store_folded_into_scan_roots()** (2 connections) — `tests/config/test_models_config.py`
- **.test_pull_root_folded_when_store_unset()** (2 connections) — `tests/config/test_models_config.py`
- **.test_store_wins_over_pull_root()** (2 connections) — `tests/config/test_models_config.py`
- **.test_no_duplicate_when_store_already_in_roots()** (2 connections) — `tests/config/test_models_config.py`
- **.test_default_config_scans_pull_root_default()** (2 connections) — `tests/config/test_models_config.py`
- **.pull_root_is_absolute()** (1 connections) — `src/hal0/config/schema.py`
- **[models] section of hal0.toml — discovery + auto-detect.** (1 connections) — `src/hal0/config/schema.py`
- **Empty means "env var / FLM default cache"; non-empty must be absolute.** (1 connections) — `src/hal0/config/schema.py`
- **Roots the discovery scan actually walks: declared ``roots`` plus the         eff** (1 connections) — `src/hal0/config/schema.py`
- *... and 5 more nodes in this community*

## Relationships

- [test_discover.py](test_discover.py.md) (5 shared connections)
- [Path](Path.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [config.py](config.py.md) (1 shared connections)
- [schema.py](schema.py.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_models_config.py`

## Audit Trail

- EXTRACTED: 60 (66%)
- INFERRED: 31 (34%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*