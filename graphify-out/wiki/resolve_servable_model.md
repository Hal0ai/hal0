# resolve_servable_model

> 13 nodes

## Key Concepts

- **resolve_servable_model()** (8 connections) — `src/hal0/registry/fallback.py`
- **fallback.py** (7 connections) — `src/hal0/registry/fallback.py`
- **looks_diffusion_or_nontext()** (5 connections) — `src/hal0/registry/fallback.py`
- **id_tokens()** (4 connections) — `src/hal0/registry/fallback.py`
- **default_model_cache_check()** (4 connections) — `src/hal0/registry/fallback.py`
- **Any** (2 connections)
- **leading_token_overlap()** (2 connections) — `src/hal0/registry/fallback.py`
- **Model-fallback heuristics — moved out of ``slots/manager.py`` (ML-2/ML-3, the P3** (1 connections) — `src/hal0/registry/fallback.py`
- **True when *model* looks like a diffusion / image / video / non-text artifact.** (1 connections) — `src/hal0/registry/fallback.py`
- **Split a model id / name / path into lower-case alphanumeric tokens.      ``gemma** (1 connections) — `src/hal0/registry/fallback.py`
- **Count of shared leading tokens between two token lists.      Used to rank fallba** (1 connections) — `src/hal0/registry/fallback.py`
- **Default predicate: registered + path-on-disk → cached.      Imports the registry** (1 connections) — `src/hal0/registry/fallback.py`
- **Resolve a slot's configured model id to one that can actually serve.      A seed** (1 connections) — `src/hal0/registry/fallback.py`

## Relationships

- [test_model_fallback.py](test_model_fallback.py.md) (4 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [get_curated](get_curated.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/registry/fallback.py`

## Audit Trail

- EXTRACTED: 35 (92%)
- INFERRED: 3 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*