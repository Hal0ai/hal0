# screen_extra_args_json

> 8 nodes · cohesion 0.25

## Key Concepts

- **screen_extra_args_json()** (6 connections) — `src/hal0/services/models_service.py`
- **test_screen_extra_args_json_rejects_shell_stripped_json()** (4 connections) — `tests/api/test_models_routes.py`
- **test_screen_extra_args_json_accepts_single_quoted_json()** (3 connections) — `tests/api/test_models_routes.py`
- **test_screen_extra_args_json_ignores_non_json_flags()** (3 connections) — `tests/api/test_models_routes.py`
- **Reject ``raw`` when a bare double-quoted JSON value was eaten by the shell.** (1 connections) — `src/hal0/services/models_service.py`
- **Bare double-quoted JSON whose quotes shlex strips is rejected with the     singl** (1 connections) — `tests/api/test_models_routes.py`
- **Correctly single-quoted JSON survives shlex-splitting and passes.** (1 connections) — `tests/api/test_models_routes.py`
- **A tune with no JSON object is untouched by the guard.** (1 connections) — `tests/api/test_models_routes.py`

## Relationships

- [test_models_routes.py](test_models_routes.py.md) (3 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/services/models_service.py`
- `tests/api/test_models_routes.py`

## Audit Trail

- EXTRACTED: 12 (60%)
- INFERRED: 8 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*