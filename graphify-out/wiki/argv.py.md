# argv.py

> 16 nodes

## Key Concepts

- **argv.py** (14 connections) — `src/hal0/slots/argv.py`
- **_split_pairs()** (8 connections) — `src/hal0/slots/argv.py`
- **_deny_managed_flags()** (6 connections) — `src/hal0/slots/argv.py`
- **_dedup()** (5 connections) — `src/hal0/slots/argv.py`
- **ResolvedArgv** (5 connections) — `src/hal0/slots/argv.py`
- **_canon()** (4 connections) — `src/hal0/slots/argv.py`
- **NormalizedArgv** (3 connections) — `src/hal0/slots/argv.py`
- **_is_flag()** (3 connections) — `src/hal0/slots/argv.py`
- **_Pair** (3 connections) — `src/hal0/slots/argv.py`
- **normalize_argv — the single dedup/last-wins pass for llama-server argv.  The lau** (1 connections) — `src/hal0/slots/argv.py`
- **Result of :func:`normalize_argv`.      ``argv`` is the deduped token list. ``rem** (1 connections) — `src/hal0/slots/argv.py`
- **True for ``--long`` and ``-x``/``-ngl`` short flags; False for values.      A le** (1 connections) — `src/hal0/slots/argv.py`
- **Group a flat token list into ``(flag, value?)`` pairs, order preserved.      A f** (1 connections) — `src/hal0/slots/argv.py`
- **Raise :class:`~hal0.errors.BadRequest` if ``tokens`` set a managed flag.      Ca** (1 connections) — `src/hal0/slots/argv.py`
- **Last-wins dedup over ``pairs``. Shared core of the two public entrypoints.** (1 connections) — `src/hal0/slots/argv.py`
- **Deduped argv plus per-flag provenance — the auditable resolution.      ``provena** (1 connections) — `src/hal0/slots/argv.py`

## Relationships

- [resolve_argv](resolve_argv.md) (6 shared connections)
- [test_argv.py](test_argv.py.md) (4 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [merge_flags](merge_flags.md) (1 shared connections)
- [planner.py](planner.py.md) (1 shared connections)
- [build_roster](build_roster.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)

## Source Files

- `src/hal0/slots/argv.py`

## Audit Trail

- EXTRACTED: 53 (91%)
- INFERRED: 5 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*