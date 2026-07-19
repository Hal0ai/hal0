# scan_and_register

> 30 nodes

## Key Concepts

- **scan_and_register()** (17 connections) — `src/hal0/registry/discover.py`
- **CuratedModel** (14 connections) — `src/hal0/registry/curated.py`
- **discover.py** (14 connections) — `src/hal0/registry/discover.py`
- **register_candidate()** (11 connections) — `src/hal0/registry/discover.py`
- **backfill_coordless()** (10 connections) — `src/hal0/registry/discover.py`
- **_maybe_register_shard_files()** (9 connections) — `src/hal0/registry/discover.py`
- **_match_curated()** (8 connections) — `src/hal0/registry/discover.py`
- **Path** (8 connections)
- **CandidateModel** (7 connections) — `src/hal0/registry/discover.py`
- **_is_skippable()** (5 connections) — `src/hal0/registry/discover.py`
- **_is_mmproj_sidecar()** (4 connections) — `src/hal0/registry/discover.py`
- **_shard_key()** (4 connections) — `src/hal0/registry/discover.py`
- **_shard_index()** (4 connections) — `src/hal0/registry/discover.py`
- **ModelRegistry** (4 connections)
- **._validate_deployable_source()** (2 connections) — `src/hal0/registry/curated.py`
- **Model** (2 connections)
- **One curated entry surfaced by the FirstRun wizard.      The wizard renders these** (1 connections) — `src/hal0/registry/curated.py`
- **Every curated model must have a deployable source: either HF pull         coordi** (1 connections) — `src/hal0/registry/curated.py`
- **Model discovery — scan filesystem roots and auto-register found models.  The sca** (1 connections) — `src/hal0/registry/discover.py`
- **One discovered file ready for registry registration.** (1 connections) — `src/hal0/registry/discover.py`
- **Return the curated entry whose ``hf_file`` equals ``filename``.** (1 connections) — `src/hal0/registry/discover.py`
- **True for a multimodal-projector (mmproj) sidecar file.      Matched by filename** (1 connections) — `src/hal0/registry/discover.py`
- **Return the ``(dir, stem, total)`` grouping key for a shard file, else ``None``.** (1 connections) — `src/hal0/registry/discover.py`
- **Return the 1-based shard index encoded in ``p``'s filename, or ``None``.** (1 connections) — `src/hal0/registry/discover.py`
- **Skip dotfiles, .tmp partials, hash-only blob names, shards, accessory dirs.** (1 connections) — `src/hal0/registry/discover.py`
- *... and 5 more nodes in this community*

## Relationships

- [test_discover.py](test_discover.py.md) (22 shared connections)
- [suggest_models](suggest_models.md) (2 shared connections)
- [HaloaiModel](HaloaiModel.md) (2 shared connections)
- [get_curated](get_curated.md) (2 shared connections)
- [models_service.py](models_service.py.md) (2 shared connections)
- [test_curated_pull_coords.py](test_curated_pull_coords.py.md) (2 shared connections)
- [connect](connect.md) (2 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [models.py](models.py.md) (1 shared connections)
- [catalog.py](catalog.py.md) (1 shared connections)
- [test_pull_routes.py](test_pull_routes.py.md) (1 shared connections)
- [_guess_capability](_guess_capability.md) (1 shared connections)

## Source Files

- `src/hal0/registry/curated.py`
- `src/hal0/registry/discover.py`

## Audit Trail

- EXTRACTED: 108 (79%)
- INFERRED: 29 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*