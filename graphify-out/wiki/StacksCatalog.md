# StacksCatalog

> 56 nodes · cohesion 0.07

## Key Concepts

- **StacksCatalog** (41 connections) — `src/hal0/stacks/__init__.py`
- **load_stacks_config()** (15 connections) — `src/hal0/config/loader.py`
- **_saber()** (12 connections) — `tests/stacks/test_stacks_catalog.py`
- **ResolvedStack** (10 connections) — `src/hal0/stacks/__init__.py`
- **save_stacks_config()** (9 connections) — `src/hal0/config/loader.py`
- **.update()** (9 connections) — `src/hal0/stacks/__init__.py`
- **StacksConfig** (8 connections) — `src/hal0/config/schema.py`
- **.create()** (8 connections) — `src/hal0/stacks/__init__.py`
- **test_stacks_catalog.py** (8 connections) — `tests/stacks/test_stacks_catalog.py`
- **._resolve_item()** (7 connections) — `src/hal0/stacks/__init__.py`
- **.test_round_trip_save_then_load()** (7 connections) — `tests/config/test_stacks_loader.py`
- **TestLoadStacksConfig** (6 connections) — `tests/config/test_stacks_loader.py`
- **catalog()** (6 connections) — `tests/stacks/test_stacks_catalog.py`
- **TestCreateAndRead** (6 connections) — `tests/stacks/test_stacks_catalog.py`
- **.test_seed_stack_is_immutable()** (6 connections) — `tests/stacks/test_stacks_catalog.py`
- **.delete()** (5 connections) — `src/hal0/stacks/__init__.py`
- **.resolve()** (5 connections) — `src/hal0/stacks/__init__.py`
- **TestUpdateAndDelete** (5 connections) — `tests/stacks/test_stacks_catalog.py`
- **__init__.py** (4 connections) — `src/hal0/stacks/__init__.py`
- **._guard_custom()** (4 connections) — `src/hal0/stacks/__init__.py`
- **.list()** (4 connections) — `src/hal0/stacks/__init__.py`
- **Path** (4 connections)
- **.test_invalid_toml_raises()** (4 connections) — `tests/config/test_stacks_loader.py`
- **.test_unknown_field_raises()** (4 connections) — `tests/config/test_stacks_loader.py`
- **.test_create_duplicate_raises_conflict()** (4 connections) — `tests/stacks/test_stacks_catalog.py`
- *... and 31 more nodes in this community*

## Relationships

- [stacks.py](stacks.py.md) (11 shared connections)
- [StackConfig](StackConfig.md) (10 shared connections)
- [BoardStore](BoardStore.md) (9 shared connections)
- [load_hal0_config](load_hal0_config.md) (6 shared connections)
- [test_drift.py](test_drift.py.md) (5 shared connections)
- [ConfigParseError](ConfigParseError.md) (4 shared connections)
- [ModelRegistry](ModelRegistry.md) (3 shared connections)
- [test_catalog_backends.py](test_catalog_backends.py.md) (3 shared connections)
- [test_installed.py](test_installed.py.md) (3 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [StackModelMeta](StackModelMeta.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `src/hal0/stacks/__init__.py`
- `tests/config/test_stacks_loader.py`
- `tests/config/test_stacks_schema.py`
- `tests/stacks/test_stacks_catalog.py`

## Audit Trail

- EXTRACTED: 198 (73%)
- INFERRED: 73 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*