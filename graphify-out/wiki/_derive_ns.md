# _derive_ns

> 12 nodes

## Key Concepts

- **_derive_ns()** (9 connections) — `src/hal0/registry/model.py`
- **test_derive_ns_blessed_for_recipe_capability_path()** (3 connections) — `tests/api/test_models_routes.py`
- **test_derive_ns_pulled_for_id_only_path()** (3 connections) — `tests/api/test_models_routes.py`
- **test_derive_ns_empty_path_is_pulled()** (3 connections) — `tests/api/test_models_routes.py`
- **test_derive_ns_blessed_root_with_only_id_segment_is_pulled()** (3 connections) — `tests/api/test_models_routes.py`
- **test_derive_ns_arbitrary_root_is_pulled()** (3 connections) — `tests/api/test_models_routes.py`
- **Return ``"blessed"`` if ``model.path`` sits under a recipe/capability     direct** (1 connections) — `src/hal0/registry/model.py`
- **Path under /var/lib/hal0/models/<recipe>/<capability>/ → blessed.** (1 connections) — `tests/api/test_models_routes.py`
- **Default pull layout /var/lib/hal0/models/<id>/<file> → pulled.** (1 connections) — `tests/api/test_models_routes.py`
- **Edge case: a Model with an unset/whitespace path must not raise.** (1 connections) — `tests/api/test_models_routes.py`
- **Only one path segment after the blessed root → not blessed.      The rule requir** (1 connections) — `tests/api/test_models_routes.py`
- **A path outside the blessed root is always pulled.** (1 connections) — `tests/api/test_models_routes.py`

## Relationships

- [test_models_routes.py](test_models_routes.py.md) (5 shared connections)
- [Model](Model.md) (2 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/model.py`
- `tests/api/test_models_routes.py`

## Audit Trail

- EXTRACTED: 19 (63%)
- INFERRED: 11 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*