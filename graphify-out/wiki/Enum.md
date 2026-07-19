# Enum

> 20 nodes · cohesion 0.13

## Key Concepts

- **Enum** (13 connections)
- **exposure.py** (9 connections) — `src/hal0/security/exposure.py`
- **AuthClass** (7 connections) — `src/hal0/security/exposure.py`
- **match_rule()** (7 connections) — `src/hal0/security/exposure.py`
- **classify()** (4 connections) — `src/hal0/security/exposure.py`
- **_exact()** (3 connections) — `src/hal0/security/exposure.py`
- **_prefix()** (3 connections) — `src/hal0/security/exposure.py`
- **_Rule** (3 connections) — `src/hal0/security/exposure.py`
- **test_unclassified_new_route_denies_by_default()** (3 connections) — `tests/security/test_exposure.py`
- **Matcher** (2 connections)
- **_outside_api_v1_mcp()** (2 connections) — `src/hal0/security/exposure.py`
- **.applies()** (2 connections) — `src/hal0/security/exposure.py`
- **Route -> :class:`AuthClass` classification table (KB-1 / §1, seam S9).  Single s** (1 connections) — `src/hal0/security/exposure.py`
- **Return the first :class:`_Rule` matching ``(method, path)``, or ``None``.      `** (1 connections) — `src/hal0/security/exposure.py`
- **Classify ``(method, path)`` against :data:`RULES`.      First-match-wins; an unm** (1 connections) — `src/hal0/security/exposure.py`
- **The four classes a route can resolve to. See module docstring.** (1 connections) — `src/hal0/security/exposure.py`
- **Match ``path`` only (no trailing-slash / sub-path variants).** (1 connections) — `src/hal0/security/exposure.py`
- **Match ``prefix`` itself or any ``prefix/...`` sub-path.      Boundary-safe: ``_p** (1 connections) — `src/hal0/security/exposure.py`
- **True for any path NOT under ``/api``, ``/v1``, or ``/mcp``.      This is the sta** (1 connections) — `src/hal0/security/exposure.py`
- **A path nobody has classified must fall back to ADMIN (not OPEN/CLIENT).** (1 connections) — `tests/security/test_exposure.py`

## Relationships

- [auth.py](auth.py.md) (3 shared connections)
- [test_exposure.py](test_exposure.py.md) (2 shared connections)
- [hermes_provision.py](hermes_provision.py.md) (1 shared connections)
- [planner.py](planner.py.md) (1 shared connections)
- [config_commands.py](config_commands.py.md) (1 shared connections)
- [die](die.md) (1 shared connections)
- [update_commands.py](update_commands.py.md) (1 shared connections)
- [pve.py](pve.py.md) (1 shared connections)
- [MemoryProvider](MemoryProvider.md) (1 shared connections)
- [test_modality.py](test_modality.py.md) (1 shared connections)
- [arbiter.py](arbiter.py.md) (1 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `src/hal0/security/exposure.py`
- `tests/security/test_exposure.py`

## Audit Trail

- EXTRACTED: 61 (92%)
- INFERRED: 5 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*