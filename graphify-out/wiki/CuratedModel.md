# CuratedModel

> 14 nodes · cohesion 0.20

## Key Concepts

- **CuratedModel** (14 connections) — `src/hal0/registry/curated.py`
- **HaloaiModel** (8 connections) — `src/hal0/registry/curated.py`
- **curated.py** (7 connections) — `src/hal0/registry/curated.py`
- **_build_curated()** (5 connections) — `src/hal0/registry/curated.py`
- **_load_haloai_seed()** (4 connections) — `src/hal0/registry/curated.py`
- **._validate_deployable_source()** (2 connections) — `src/hal0/registry/curated.py`
- **BaseModel** (2 connections)
- **Curated model catalogue — the FirstRun wizard's pick list.  The catalogue is a s** (1 connections) — `src/hal0/registry/curated.py`
- **One upstream-routed model imported from the haloai catalogue.      Unlike :class** (1 connections) — `src/hal0/registry/curated.py`
- **Read the frozen haloai snapshot from disk. Cached after first call.** (1 connections) — `src/hal0/registry/curated.py`
- **Merge the hand-rolled curated list with the haloai seed.      Local :data:`CURAT** (1 connections) — `src/hal0/registry/curated.py`
- **# NOTE: the catalogue lives in code (not a TOML file on disk) on purpose:** (1 connections) — `src/hal0/registry/curated.py`
- **Every curated model must have a deployable source: either HF pull         coordi** (1 connections) — `src/hal0/registry/curated.py`
- **One curated entry surfaced by the FirstRun wizard.      The wizard renders these** (1 connections) — `src/hal0/registry/curated.py`

## Relationships

- [catalog.py](catalog.py.md) (3 shared connections)
- [get_curated](get_curated.md) (2 shared connections)
- [models.py](models.py.md) (2 shared connections)
- [suggest_models](suggest_models.md) (2 shared connections)
- [register_candidate](register_candidate.md) (1 shared connections)
- [_match_curated](_match_curated.md) (1 shared connections)
- [test_pull_routes.py](test_pull_routes.py.md) (1 shared connections)
- [test_curated.py](test_curated.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/curated.py`

## Audit Trail

- EXTRACTED: 42 (86%)
- INFERRED: 7 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*